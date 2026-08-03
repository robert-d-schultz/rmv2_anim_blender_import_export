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
from . import rmv2_format as rf
from . import skeleton
from . import utils
from .properties import VERTEX_FORMAT_FROM_INT, ensure_default_textures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_custom_normals(me, normals_b: np.ndarray):
    if np.allclose(normals_b, 0.0):
        return
    normals = utils.normalize_rows(normals_b, fallback=(0.0, 0.0, 0.0))
    if hasattr(me, "use_auto_smooth"):      # Blender <= 4.0
        me.use_auto_smooth = True
    me.normals_split_custom_set_from_vertices(normals.tolist())


def _add_uv_layer(me, name: str, per_vertex_uv: np.ndarray,
                  loop_vidx: np.ndarray):
    layer = me.uv_layers.new(name=name, do_init=False)
    if layer is None:      # exceeded max uv layers
        return
    layer.data.foreach_set("uv",
                           per_vertex_uv[loop_vidx].ravel().astype(np.float32))


def _add_colour_attribute(me, colours: np.ndarray):
    ca = me.color_attributes.new(name="Colour", type="BYTE_COLOR",
                                 domain="POINT")
    flat = np.clip(colours, 0.0, 1.0).ravel().astype(np.float32)
    try:
        ca.data.foreach_set("color_srgb", flat)
    except (AttributeError, TypeError):
        ca.data.foreach_set("color", flat)


def _add_vertex_groups(obj, mesh: rf.RmvMeshData, bone_names: dict):
    k = mesh.bone_weights.shape[1] if mesh.bone_weights.ndim == 2 else 0
    if not k or mesh.vertex_count == 0:
        return
    idx = mesh.bone_indices
    wgt = mesh.bone_weights
    used = np.unique(idx[wgt > 0.0])
    for bone in used.tolist():
        name = bone_names.get(bone, f"bone_{bone}")
        group = obj.vertex_groups.new(name=name)
        # A bone can appear in several influence slots of one vertex; sum.
        wsum = np.where(idx == bone, wgt, 0.0).sum(axis=1)
        verts = np.nonzero(wsum > 0.0)[0]
        weights = wsum[verts]
        # Weights are byte-quantized, so batching identical values is cheap.
        for value in np.unique(weights):
            vs = verts[weights == value]
            group.add(vs.tolist(), float(value), "REPLACE")


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
    ensure_default_textures(s)
    s.extra_json = _build_extra_json(material, model)


def _build_mesh_object(model, version: int, name: str, scale: float,
                       bone_names: dict, options: dict):
    mesh_data = model.mesh
    material = model.material

    me = bpy.data.meshes.new(name)
    n = mesh_data.vertex_count

    # File positions are pivot-relative: the game renders raw + pivot
    # (AssetEditor Rmv2MeshNode: ModelMatrix * Translation(PivotPoint)),
    # so they map straight onto mesh-local coords with the pivot as the
    # object origin.
    pivot_b = np.array(
        utils.game_to_blender_v(material.pivot), np.float32) * scale
    pos_b = utils.game_to_blender(mesh_data.positions) * scale

    me.vertices.add(n)
    me.vertices.foreach_set("co", pos_b.ravel())

    # The game->Blender map is a reflection (det -1), so winding must be
    # reversed once to keep faces front-facing (see utils.py).
    tris = utils.reverse_winding(
        mesh_data.indices.reshape(-1, 3)).astype(np.int32)
    nloops = tris.size
    me.loops.add(nloops)
    me.loops.foreach_set("vertex_index", tris.ravel())
    npolys = len(tris)
    me.polygons.add(npolys)
    me.polygons.foreach_set("loop_start",
                            np.arange(0, nloops, 3, dtype=np.int32))
    me.validate(verbose=False, clean_customdata=False)

    if len(me.polygons):
        me.polygons.foreach_set("use_smooth",
                                np.ones(len(me.polygons), dtype=np.int8))

    # Per-loop attributes are gathered through the *actual* loop->vertex
    # mapping, so they stay correct even if validate() dropped bad faces.
    loop_vidx = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", loop_vidx)

    if n:
        _add_uv_layer(me, "UVMap", utils.flip_uv_v(mesh_data.uv0), loop_vidx)
        if mesh_data.raw_format == rf.VF_STATIC:
            _add_uv_layer(me, "UVMap_1", utils.flip_uv_v(mesh_data.uv1),
                          loop_vidx)
        if _material_has_colour(mesh_data.raw_format, version):
            _add_colour_attribute(me, mesh_data.colours)
        _set_custom_normals(me, utils.game_to_blender(mesh_data.normals))

    me.update()

    obj = bpy.data.objects.new(name, me)
    obj.location = pivot_b.tolist()

    _add_vertex_groups(obj, mesh_data, bone_names)
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


def _find_layer_collection(layer_collection, collection):
    if layer_collection.collection == collection:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, collection)
        if found is not None:
            return found
    return None


def _show_only_most_detailed_lod(context, lod_collections: list):
    """Hide every LOD collection but the most detailed one (index 0) in the
    current view layer, so the viewport isn't cluttered with overlapping
    LODs right after import. Reversible via the outliner's eye icon."""
    if len(lod_collections) < 2:
        return
    root_layer_col = context.view_layer.layer_collection
    for col in lod_collections[1:]:
        layer_col = _find_layer_collection(root_layer_col, col)
        if layer_col is not None:
            layer_col.hide_viewport = True


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

def _adopt_armature(root, armature):
    """Move an existing armature into the model's root collection so the
    skeleton travels with the model it now drives.  Links into other RMV2
    roots are kept - one skeleton may serve several imported models."""
    for col in list(armature.users_collection):
        if not col.rmv2.is_rmv2_root:
            col.objects.unlink(armature)
    root.objects.link(armature)


def import_file(context, filepath: str, options: dict):
    """Import one .rigid_model_v2 file. Returns (root_collection, stats)."""
    with open(filepath, "rb") as handle:
        data = handle.read()
    rmv = rf.load(data)

    stem = os.path.splitext(os.path.basename(filepath))[0]
    scale = options.get("global_scale", 1.0)

    root = bpy.data.collections.new(stem)
    context.scene.collection.children.link(root)
    root.rmv2.is_rmv2_root = True
    if str(rmv.version) in {"6", "7", "8"}:
        root.rmv2.version = str(rmv.version)
    root.rmv2.skeleton_name = rmv.skeleton_name

    # An already-selected armature (e.g. from a .anim import) supplies the
    # bone names and the meshes get attached to it below. Without one,
    # groups get the standard bone_<i> names - attachment point names are
    # deliberately NOT used (they usually mirror the skeleton, but the
    # .anim bone table is the authority; a later .anim import renames).
    armature = None
    if options.get("attach_armature", True):
        armature = skeleton.find_context_armature(context)
    if armature is not None:
        _adopt_armature(root, armature)

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
    attach_by_matrix_index = (
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
        col = bpy.data.collections.new(f"{stem}_lod{lod_index}")
        root.children.link(col)
        lod_collections.append(col)
        col.rmv2.is_lod = True
        col.rmv2.lod_level = lod.lod_level if rmv.version >= 7 else lod_index
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

    _show_only_most_detailed_lod(context, lod_collections)

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
