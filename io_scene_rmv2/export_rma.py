"""Exporter: Blender objects + armature -> .rigid_model_animation.

The inverse of import_rma, and just as thin: the objects are written by
the .animatable_rigid_model writer and the armature's action is appended
as a headerless .anim, which is all this format is.

The animation is not optional here - a .rigid_model_animation with no
animation is just a .rigid_model - so the export is refused without an
armature rather than writing a file the game would not recognise.
"""

from __future__ import annotations

from . import anim_format as anf
from . import arm_format as armf
from . import export_anim, export_arm, skeleton
from .properties import chosen_version, root_format_version


class RmaExportError(Exception):
    pass


def export_file(context, filepath: str, options: dict):
    """Write one .rigid_model_animation. Returns (stats, warnings)."""
    warnings: list = []
    root, objects = export_arm._gather_objects(context, options)
    if not objects:
        raise RmaExportError(
            "Nothing to export: select the mesh objects, or make the "
            "model's collection active")

    armature = skeleton.find_context_armature(context)
    if armature is None:
        raise RmaExportError(
            "This format is an animated rigid model - its objects are "
            "welded to the bones of a skeleton stored in the same file - "
            "so it cannot be written without an armature. Select the "
            "model's armature and try again")

    options = dict(options, arm_version=int(chosen_version(
        options, "arm_version", root_format_version(root, "ARM", "5"))))
    arm = armf.ArmFile()
    for obj in objects:
        mesh = export_arm._build_mesh(context, obj, options, warnings)
        if mesh is None:
            continue
        # Always the animatable layout: every object carries the index of
        # the bone it rides on.
        mesh.bone_index = export_arm._bone_index(obj, armature, True)
        arm.meshes.append(mesh)

    if not arm.meshes:
        raise RmaExportError("No exportable meshes")

    # The embedded animation is the headerless Shogun 2 / Empire layout
    # (anim_format's synthetic version 0), which is what every vanilla
    # .rigid_model_animation carries.
    anim_options = dict(options)
    anim_options["version"] = anf.SHOGUN2_NO_HEADER_VERSION
    anim, _, frames = export_anim.build_anim(context, anim_options,
                                             warnings)
    arm.trailing = anf.save(anim)

    blob = armf.save(arm)
    with open(filepath, "wb") as handle:
        handle.write(blob)

    stats = {
        "meshes": len(arm.meshes),
        "vertices": sum(m.vertex_count for m in arm.meshes),
        "triangles": sum(len(m.indices) // 3 for m in arm.meshes),
        "bones": len(anim.bones),
        "frames": frames,
        "bytes": len(blob),
    }
    return stats, warnings
