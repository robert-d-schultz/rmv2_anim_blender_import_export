"""Importer: Shogun 2 .animatable_rigid_model / .rigid_model -> Blender.

An ARM file is a flat list of objects, each welded to exactly one bone of
the model's .anim skeleton - Shogun 2's way of animating jointed props
(siege engines, ballistae).  There are no LODs inside the file and no
per-vertex skinning; the LOD ladder is expressed as separate files
(`..._lod1`, `..._lod2`).

Plain `.rigid_model` is the same container minus the per-object bone
index (the Empire/Napoleon era format, still used in Shogun 2 for static
scenery).  Those objects import unattached.

The scene layout mirrors the .rigid_model_v2 importer so the two feel the
same and the RMV2 panels keep working:

    <name>              collection, rmv2.is_rmv2_root
      <name>_lod0       collection (any child of a root is a LOD)
        <object>        mesh objects, one per file object

Each object is bound to its bone with a Child Of constraint (the same
mechanism the RMV2 importer uses for matrix_index-driven pieces) rather
than a vertex group, because that is what the format actually means.

Coordinates are converted with the shared mapping in utils.py; the ARM
vertex block stores plain float32 positions and a full float tangent
frame, so nothing is quantized on the way in.
"""

from __future__ import annotations

import os

import bpy
import numpy as np

from . import arm_format as armf
from . import materials, mesh_build, scene_layout, skeleton, utils
from .properties import fill_shader_params, set_format_version


class ArmImportError(Exception):
    pass


def _mesh_name(mesh: armf.ArmMesh, index: int) -> str:
    """These objects carry no name of their own - only a texture set and
    (in the animatable form) a bone index - so name them after the bone
    they ride on, or just by position for plain .rigid_model."""
    if mesh.bone_index is None:
        return f"object_{index:02d}"
    return f"object_{index:02d}_bone{mesh.bone_index}"


def _build_mesh_object(mesh: armf.ArmMesh, name: str, scale: float,
                       warnings: list):
    positions = utils.game_to_blender(mesh.positions) * scale
    # The conversion mirrors once, so winding flips - see utils.py.
    indices = np.asarray(mesh.indices, np.int64)
    tris = utils.reverse_winding(
        indices[:(len(indices) // 3) * 3].reshape(-1, 3))
    try:
        me, loop_vidx = mesh_build.build_mesh(name, positions, tris, warnings)
    except mesh_build.MeshBuildError as exc:
        raise ArmImportError(str(exc)) from exc

    for uv_name, data in (("UVMap", mesh.uv0), ("UVMap_1", mesh.uv1)):
        if len(data):
            mesh_build.add_uv_layer(me, uv_name, utils.flip_uv_v(data),
                                    loop_vidx)

    if len(mesh.colours):
        mesh_build.add_colour_attribute(me, mesh.colours)

    mesh_build.set_custom_normals(
        me, utils.normalize_rows(utils.game_to_blender(mesh.normals)))

    me.update()
    return bpy.data.objects.new(name, me)


def _fill_settings(obj, mesh: armf.ArmMesh, index: int):
    """Store what the RMV2 panels can show, plus the ARM-only bits."""
    s = obj.rmv2
    s.model_name = _mesh_name(mesh, index)[:31]
    s.matrix_index = -1 if mesh.bone_index is None else mesh.bone_index
    s.textures_initialized = True
    # Per object, not per file: three of naval_cannon_12lb_lod4's four
    # objects carry 13 parameters and the fourth carries none. Filling
    # it even when empty is what keeps that fourth object empty on the
    # way back out - see properties.fill_shader_params.
    fill_shader_params(s, mesh.float_params, mesh.vec4_params)
    for slot_name, path in zip(armf.TEXTURE_SLOTS, mesh.textures):
        if not path:
            continue
        slot = s.textures.add()
        slot.set_type_from_int(_TEXTURE_TYPE_BY_SLOT[slot_name])
        slot.path = path


_TEXTURE_TYPE_BY_SLOT = {
    "diffuse": 0,
    "normal": 1,
    "gloss": 12,
    "ao": 5,
}


def _skeleton_name_for(arm, filepath: str, stem: str,
                       warnings: list) -> str:
    """The skeleton this model rides, or "" - never a guess.

    Nothing in either format names a skeleton, so there are only two
    honest answers. A plain `.rigid_model` has no bone index on any
    object, which is the whole of what "not animatable" means: it is
    scenery, and it rides nothing. An animatable one does ride a
    skeleton, but the file does not say which, and CA's convention is an
    `.anim` of the same name beside it - so that is used only when the
    file is actually there to be checked.

    Filling it in regardless put "mountainb.anim" on a Napoleon campaign
    mountain, which has no bones at all.
    """
    if all(mesh.bone_index is None for mesh in arm.meshes):
        return ""
    sibling = os.path.join(os.path.dirname(filepath), stem + ".anim")
    if os.path.isfile(sibling):
        return stem + ".anim"
    warnings.append(
        f"{stem}: this format does not name its skeleton, and no "
        f"'{stem}.anim' sits beside it. Set Skeleton on the model "
        "collection if you know which one it rides")
    return ""


def import_file(context, filepath: str, options: dict):
    """Import one .animatable_rigid_model. Returns (root_collection, stats).
    """
    with open(filepath, "rb") as handle:
        data = handle.read()
    arm = armf.load(data)

    warnings: list[str] = []
    stem = os.path.splitext(os.path.basename(filepath))[0]
    scale = options.get("global_scale", 1.0)

    root = scene_layout.new_root(
        context, stem, _skeleton_name_for(arm, filepath, stem, warnings))
    set_format_version(root.rmv2, "ARM", arm.version)
    lod = scene_layout.new_lod(root, f"{stem}_lod0", 0)

    armature = None
    if options.get("attach_armature", True):
        armature = skeleton.find_context_armature(context)
    bone_names = skeleton.bone_name_by_index(armature) if armature else {}

    objects = []
    for i, mesh in enumerate(arm.meshes):
        name = _mesh_name(mesh, i)
        obj = _build_mesh_object(mesh, name, scale, warnings)
        lod.objects.link(obj)
        _fill_settings(obj, mesh, i)

        if options.get("build_materials", True):
            texture_root = options.get("texture_root", "")
            pairs = [(_TEXTURE_TYPE_BY_SLOT[slot], path)
                     for slot, path in zip(armf.TEXTURE_SLOTS,
                                           mesh.textures) if path]
            if pairs:
                material = materials.build_material(
                    name, pairs, "NONE", texture_root)
                if material is not None:
                    obj.data.materials.append(material)

        if armature is not None and mesh.bone_index is not None:
            bone_name = bone_names.get(mesh.bone_index)
            if bone_name is None:
                warnings.append(
                    f"{name}: bone index {mesh.bone_index} is not on "
                    f"armature '{armature.name}' "
                    f"({len(bone_names)} bones); left unattached")
            else:
                skeleton.attach_matrix_index_mesh(obj, armature, bone_name)
        objects.append(obj)

    stats = {
        "meshes": len(arm.meshes),
        "vertices": sum(m.vertex_count for m in arm.meshes),
        "triangles": sum(len(m.indices) // 3 for m in arm.meshes),
        "attached": sum(1 for o in objects if o.constraints),
    }
    return root, stats, warnings
