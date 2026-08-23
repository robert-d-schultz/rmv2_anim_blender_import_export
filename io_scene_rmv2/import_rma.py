"""Importer: Empire/Napoleon .rigid_model_animation -> Blender.

This format is not a new container at all: it is exactly the
`.animatable_rigid_model` object list, followed by a whole headerless
`.anim`.  Empire uses it for self-contained animated props - the
destruction sequences for cannon carriages, ships and campaign buildings
- where the geometry and the animation that shatters it ship as one
file.  Reading it is therefore the two existing readers side by side,
and all 725 of Empire's files re-save byte for byte with no new format
code (see arm_format.load(allow_trailing=True)).

The scene it builds is the union of what the two importers build:

    <name>              collection, rmv2.is_rmv2_root
      <armature>        from the embedded .anim, with its action
      <name>_lod0       collection, rmv2.is_lod
        <object>        one mesh object per file object, each welded to
                        one bone with a Child Of constraint

Because the skeleton travels inside the file there is nothing to select
first and nothing to guess: the armature is built here and the objects
are attached to it in the same pass.
"""

from __future__ import annotations

import os

from . import anim_format as anf
from . import arm_format as armf
from . import import_anim, import_arm, materials, scene_layout, skeleton


class RmaImportError(Exception):
    pass


def import_file(context, filepath: str, options: dict):
    """Import one .rigid_model_animation. Returns (root, stats, warnings).
    """
    with open(filepath, "rb") as handle:
        data = handle.read()
    arm = armf.load(data, allow_trailing=True)

    warnings: list = []
    stem = os.path.splitext(os.path.basename(filepath))[0]
    scale = options.get("global_scale", 1.0)

    anim = None
    if arm.trailing:
        try:
            anim = anf.load(arm.trailing)
        except anf.AnimFormatError as exc:
            warnings.append(
                f"{stem}: the objects read fine but the animation after "
                f"them did not ({exc}); importing the meshes only")
    else:
        warnings.append(
            f"{stem}: no animation after the objects - this is a plain "
            "rigid model saved under the animated extension")

    root = scene_layout.new_root(context, stem, stem)
    root.rmv2.arm_version = arm.version
    lod = scene_layout.new_lod(root, f"{stem}_lod0", 0)

    arm_obj = None
    frames = 0
    if anim is not None and anim.bones:
        resolved = anf.resolve_all(anim)
        arm_obj = import_anim.build_armature(
            context, anim, resolved, stem, scale, warnings,
            link_collection=root)
        frames = import_anim.apply_animation(
            context, arm_obj, anim, resolved, stem, scale, warnings)

    bone_names = skeleton.bone_name_by_index(arm_obj) if arm_obj else {}

    objects = []
    for i, mesh in enumerate(arm.meshes):
        name = import_arm._mesh_name(mesh, i)
        obj = import_arm._build_mesh_object(mesh, name, scale, warnings)
        lod.objects.link(obj)
        import_arm._fill_settings(obj, mesh, i)

        if options.get("build_materials", True):
            pairs = [(import_arm._TEXTURE_TYPE_BY_SLOT[slot], path)
                     for slot, path in zip(armf.TEXTURE_SLOTS,
                                           mesh.textures) if path]
            if pairs:
                material = materials.build_material(
                    name, pairs, "NONE", options.get("texture_root", ""))
                if material is not None:
                    obj.data.materials.append(material)

        if arm_obj is not None and mesh.bone_index is not None:
            bone_name = bone_names.get(mesh.bone_index)
            if bone_name is None:
                warnings.append(
                    f"{name}: bone index {mesh.bone_index} is not in this "
                    f"file's own skeleton ({len(bone_names)} bones); left "
                    "unattached")
            else:
                skeleton.attach_matrix_index_mesh(obj, arm_obj, bone_name)
        objects.append(obj)

    stats = {
        "meshes": len(arm.meshes),
        "vertices": sum(m.vertex_count for m in arm.meshes),
        "triangles": sum(len(m.indices) // 3 for m in arm.meshes),
        "attached": sum(1 for o in objects if o.constraints),
        "bones": len(anim.bones) if anim is not None else 0,
        "frames": frames,
    }
    return root, stats, warnings
