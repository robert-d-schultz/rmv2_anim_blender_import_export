"""Importer: .rigid_model_v2 -> Blender objects/collections.

Scene layout created per file:

    <name>                  (collection, rmv2.is_rmv2_root, version/skeleton)
      <name>_lod0           (collection, rmv2.is_lod, camera distance...)
          mesh objects      (rmv2 object settings filled in)
      <name>_lod1
      ...
      <name>_attach         (optional attachment point empties)

Vertices are converted to Blender space (the conversion mirrors once, so
triangle winding is reversed to compensate - see utils.py), UV V is
flipped, normals become custom split
normals, bone weights become vertex groups named after the selected
armature's bones, else the standard bone_<i> fallback (a later .anim
import renames them). File vertices are pivot-relative (the game renders
raw + pivot), so they become the mesh-local coordinates and the pivot
becomes the object origin. With an armature selected the meshes are
parented to it with an armature modifier and the armature is moved into
the root collection so the skeleton travels with the model. A building
-like file (blank or "building" skeleton name) with a "building" armature
selected instead rigidly constrains each unweighted mesh to its
material's matrix_index bone via a Child Of constraint (destructible
-building pieces - genuinely unrigged, so no vertex groups/armature
modifier are involved) - see `attach_by_matrix_index` and
`skeleton.attach_to_bone`.
"""

from __future__ import annotations

import json
import os

import bpy
import numpy as np
from mathutils import Matrix

from . import materials as rmv2_materials
from . import mesh_build
from . import rmv2_format as rf
from . import scene_layout
from . import skeleton
from . import utils
from .properties import VERTEX_FORMAT_FROM_INT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _material_has_colour(fmt: int, version: int) -> bool:
    if fmt == rf.VF_STATIC or fmt == rf.VF_CUSTOM_TERRAIN2:
        return True
    return fmt in (rf.VF_WEIGHTED, rf.VF_CINEMATIC) and version == 8


def _bytes_to_hex(raw: bytes) -> str:
    return bytes(raw).hex()


def _build_extra_json(material, model) -> str:
    """Preserve rarely-edited raw fields for lossless re-export."""
    extra = {}
    if isinstance(material, rf.WeightedMaterial):
        identity = (rf.IDENTITY_3X4, rf.IDENTITY_3X4, rf.IDENTITY_3X4)
        matrices = tuple(tuple(float(v) for v in m)
                         for m in material.matrices)
        if matrices != identity:
            extra["matrices"] = [list(m) for m in matrices]
        if tuple(material.padding2) != (0, 0):
            extra["padding2"] = list(material.padding2)
        if any(material.padding124):
            extra["padding124"] = _bytes_to_hex(material.padding124)
        if material.string_params:
            extra["string_params"] = [[i, v]
                                      for i, v in material.string_params]
        if material.float_params:
            extra["float_params"] = [[i, float(v)]
                                     for i, v in material.float_params]
        other_ints = [[i, v] for i, v in material.int_params
                      if i != rf.INT_PARAM_ALPHA]
        if other_ints:
            extra["int_params"] = other_ints
        if material.vec4_params:
            extra["vec4_params"] = [[i, list(v)]
                                    for i, v in material.vec4_params]
    elif isinstance(material, rf.DecalMaterial):
        # The path is already a texture slot; what is left is the float
        # block, whose length is which decal version this is.
        extra["material_kind"] = "decal"
        extra["decal_values"] = [float(v) for v in material.values]
    elif isinstance(material, rf.TerrainTileMaterial):
        extra["material_kind"] = "terrain_tile"
        extra["terrain_words"] = [int(v) for v in material.unknowns]
    elif isinstance(material, rf.EmptyMaterial):
        # Nothing to keep but the fact that there was no header at all.
        extra["material_kind"] = "empty"
    elif isinstance(material, rf.Shogun2Material):
        # A Shogun 2 material is a run of fixed-width fields whose exact
        # composition depends on the material id, and several ids have
        # trailing bytes we do not understand. Keeping the block verbatim
        # means those survive a Blender round-trip; export patches the
        # fields the user can actually edit back into it.
        extra["s2_material_raw"] = _bytes_to_hex(material.raw)
        extra["s2_material_version"] = material.version
    if getattr(material, "trailing", b""):
        # Cloth, rope and collision shapes: a simulation block this
        # add-on does not interpret and Blender could not rebuild.
        extra["material_trailing"] = _bytes_to_hex(material.trailing)
    if model.section_tail:
        extra["section_tail"] = _bytes_to_hex(model.section_tail)
    if model.declared_vertex_count is not None:
        extra["declared_vertex_count"] = int(model.declared_vertex_count)
    if any(model.shader_extra):
        extra["shader_extra"] = _bytes_to_hex(model.shader_extra)
    if any(model.shader_zero):
        extra["shader_zero"] = _bytes_to_hex(model.shader_zero)
    return json.dumps(extra) if extra else ""


def _fill_object_settings(obj, model, material):
    s = obj.rmv2
    s.model_name = material.model_name[:31]
    s.vertex_format = VERTEX_FORMAT_FROM_INT.get(
        material.vertex_format, "AUTO")
    mat_name = rf.MATERIAL_NAMES.get(material.material_id)
    if mat_name is not None:
        s.material_id = mat_name
    else:
        s.material_id = "OTHER"
        s.material_id_raw = material.material_id
    alpha = material.get_int_param(rf.INT_PARAM_ALPHA)
    if alpha is None:
        s.alpha_mode = "NONE"
    else:
        s.alpha_mode = "OPAQUE" if alpha == 0 else "TRANSPARENT"
    s.render_flag = model.render_flag
    s.shader_name = model.shader_name.rstrip("\0")
    s.texture_directory = material.texture_directory
    s.filters = material.filters
    s.matrix_index = material.matrix_index
    s.parent_matrix_index = material.parent_matrix_index
    for ttype, path in material.textures:
        slot = s.textures.add()
        slot.set_type_from_int(ttype)
        slot.path = path
    # Imported objects keep exactly what the file had - the 4 core-type
    # defaults are only for brand new objects (see ensure_default_textures),
    # so just mark this one settled instead of backfilling any gaps.
    s.textures_initialized = True
    s.extra_json = _build_extra_json(material, model)


def _build_mesh_object(model, version: int, name: str, scale: float,
                       bone_names: dict, options: dict):
    mesh_data = model.mesh
    material = model.material

    n = mesh_data.vertex_count

    # File positions are pivot-relative: the game renders raw + pivot
    # (AssetEditor Rmv2MeshNode: ModelMatrix * Translation(PivotPoint)),
    # so they map straight onto mesh-local coords with the pivot as the
    # object origin.
    pivot_b = np.array(
        utils.game_to_blender_v(material.pivot), np.float32) * scale
    pos_b = utils.game_to_blender(mesh_data.positions) * scale

    # The game->Blender map is a reflection (det -1), so winding must be
    # reversed once to keep faces front-facing (see utils.py).
    tris = utils.reverse_winding(mesh_data.indices.reshape(-1, 3))
    me, loop_vidx = mesh_build.build_mesh(name, pos_b, tris)

    if n:
        mesh_build.add_uv_layer(me, "UVMap", utils.flip_uv_v(mesh_data.uv0),
                                loop_vidx)
        if mesh_data.raw_format == rf.VF_STATIC:
            mesh_build.add_uv_layer(me, "UVMap_1",
                                    utils.flip_uv_v(mesh_data.uv1), loop_vidx)
        if _material_has_colour(mesh_data.raw_format, version):
            mesh_build.add_colour_attribute(me, mesh_data.colours)
        if mesh_data.extras:
            # Vegetation and tree billboards carry channels no standard
            # mesh field can hold; they ride along as point attributes.
            mesh_build.add_extra_attributes(me, mesh_data.extras)
        mesh_build.set_custom_normals(
            me, utils.game_to_blender(mesh_data.normals))

    me.update()

    obj = bpy.data.objects.new(name, me)
    obj.location = pivot_b.tolist()

    mesh_build.add_vertex_groups(obj, mesh_data.bone_indices,
                                 mesh_data.bone_weights, bone_names)
    _fill_object_settings(obj, model, material)

    if options.get("build_materials", True):
        alpha = obj.rmv2.alpha_mode
        bmat = rmv2_materials.build_material(
            name, list(material.textures), alpha,
            options.get("texture_root", ""))
        me.materials.append(bmat)

    return obj


def _attach_matrix_to_blender(matrix12, scale: float) -> Matrix:
    """3x4 row-major game matrix -> Blender world matrix (conjugated by
    the game->Blender map from utils.py)."""
    m = Matrix((matrix12[0:4], matrix12[4:8], matrix12[8:12],
                (0.0, 0.0, 0.0, 1.0)))
    conv = Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    out = conv @ m @ conv.inverted()
    out.translation = out.translation * scale
    return out


def _collect_attach_points(rmv: rf.RmvFile):
    """First non-empty attachment point list in the file (they are repeated
    per mesh and normally identical)."""
    for lod in rmv.lods:
        for model in lod.models:
            aps = getattr(model.material, "attachment_points", None)
            if aps:
                return aps
    return []


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def import_file(context, filepath: str, options: dict):
    """Import one .rigid_model_v2 file. Returns (root_collection, stats)."""
    with open(filepath, "rb") as handle:
        data = handle.read()
    rmv = rf.load(data)

    stem = os.path.splitext(os.path.basename(filepath))[0]
    scale = options.get("global_scale", 1.0)

    root = scene_layout.new_root(context, stem, rmv.skeleton_name)
    if str(rmv.version) in {"1", "2", "6", "7", "8"}:
        root.rmv2.version = str(rmv.version)

    # An already-selected armature (e.g. from a .anim import) supplies the
    # bone names and the meshes get attached to it below. Without one,
    # groups get the standard bone_<i> names - attachment point names are
    # deliberately NOT used (they usually mirror the skeleton, but the
    # .anim bone table is the authority; a later .anim import renames).
    armature = None
    if options.get("attach_armature", True):
        armature = skeleton.find_context_armature(context)
    if armature is not None:
        scene_layout.adopt_armature(root, armature)

    # A "building" armature's meshes are destructible-building pieces:
    # rigid, matrix_index-driven, never truly bone-weighted, even though
    # an armature is attached - so they must not count as "rigged" for
    # the auto-LOD vertex-format defaults below.
    armature_is_building = (
        armature is not None
        and armature.data.rmv2.skeleton_name.strip().lower() == "building")

    # Auto-LOD override rows (Export > Generate LODs) start out populated
    # with sane defaults - rigged models default their farther LODs to
    # fewer bone influences - rather than an empty list the user has to
    # fill in by hand; see export_rmv2.default_lod_overrides.
    from .export_rmv2 import default_lod_overrides
    default_lod_overrides(
        root, rigged=armature is not None and not armature_is_building)

    attach_points = _collect_attach_points(rmv)
    bone_names = {}
    if armature is not None:
        bone_names = skeleton.bone_name_by_index(armature)

    # Destructible-building pieces have no bone weights of their own -
    # they're rigid meshes that follow a single bone/matrix, named by the
    # material's matrix_index field. If this file is itself building-like
    # (blank or "building" skeleton name - a real named skeleton's
    # matrix_index wouldn't mean this) too, each unweighted mesh gets
    # rigidly constrained to that bone below (skeleton.attach_to_bone)
    # instead of the normal vertex-group-based attach.
    #
    # Shogun 2 works this way for everything: it has no per-vertex
    # skinning at all, so every mesh is welded to the single bone its
    # material names, and its vertices are stored in that bone's space.
    attach_by_matrix_index = (
        armature is not None and rmv.version in rf.SHOGUN2_VERSIONS
    ) or (
        armature_is_building
        and rmv.skeleton_name.strip().lower() in ("", "building"))
    for ap in attach_points:
        entry = root.rmv2.attach_points.add()
        entry.name = ap.name
        entry.bone_index = ap.bone_index
        entry.matrix = [float(v) for v in ap.matrix]

    lods = rmv.lods
    if options.get("import_lods", "ALL") == "FIRST":
        lods = lods[:1]

    stats = {"lods": 0, "meshes": 0, "vertices": 0, "triangles": 0}
    lod_collections = []
    for lod_index, lod in enumerate(lods):
        col = scene_layout.new_lod(
            root, f"{stem}_lod{lod_index}",
            lod.lod_level if rmv.version >= 7 else lod_index)
        lod_collections.append((lod_index, col))
        col.rmv2.camera_distance = lod.camera_distance
        col.rmv2.quality_level = lod.quality_level
        stats["lods"] += 1

        for mesh_index, model in enumerate(lod.models):
            name = model.material.model_name or f"{stem}_mesh{mesh_index}"
            if len(lods) > 1:
                name = f"{name}_lod{lod_index}"
            obj = _build_mesh_object(model, rmv.version, name, scale,
                                     bone_names, options)
            col.objects.link(obj)
            if attach_by_matrix_index and not obj.vertex_groups:
                bone_name = bone_names.get(model.material.matrix_index)
                if bone_name:
                    skeleton.attach_matrix_index_mesh(obj, armature,
                                                      bone_name)
            elif armature is not None and obj.vertex_groups:
                skeleton.attach_mesh(obj, armature)
            stats["meshes"] += 1
            stats["vertices"] += model.mesh.vertex_count
            stats["triangles"] += len(model.mesh.indices) // 3

    scene_layout.show_only_most_detailed_lod(context, lod_collections)

    if options.get("create_attach_empties", False) and attach_points:
        ap_col = bpy.data.collections.new(f"{stem}_attach")
        root.children.link(ap_col)
        for ap in attach_points:
            empty = bpy.data.objects.new(f"ap_{ap.name}", None)
            empty.empty_display_type = "PLAIN_AXES"
            empty.empty_display_size = 0.05
            empty.matrix_world = _attach_matrix_to_blender(ap.matrix, scale)
            empty["rmv2_bone_index"] = ap.bone_index
            ap_col.objects.link(empty)

    return root, stats
