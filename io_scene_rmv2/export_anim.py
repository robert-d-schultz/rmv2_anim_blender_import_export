"""Exporter: Blender armature -> .anim.

Two modes:

* ANIMATION - sample the armature's evaluated pose (constraints and all)
  over the scene frame range, one file frame per scene frame.
* BINDPOSE  - write the armature's rest pose as a skeleton file (two
  identical frames, matching the vanilla animations/skeletons/*.anim
  layout).

Every bone is written as a dynamic track (no mapping/static-frame
compression), which every game version accepts.  Bone order and parent
indices come from the `rmv2_bone_index` custom properties stamped at
import time, falling back to Blender's bone order for hand-made armatures.

Coordinate conversion is the inverse of the importer's:
t_game = (x, z, -y), q_game xyzw = (qx, qz, -qy, qw).
"""

from __future__ import annotations

import numpy as np

import bpy
from mathutils import Matrix

from . import anim_format as af
from . import skeleton


class AnimExportError(Exception):
    pass


def blender_to_game_translation(v):
    return (-float(v[0]), float(v[2]), -float(v[1]))


def blender_to_game_quaternion_xyzw(q):
    """Blender Quaternion (w,x,y,z) -> file order xyzw. Conjugation by a
    reflection: vector part maps through the linear map negated
    (pseudo-vector rule) - the exact inverse of the import conversion."""
    return (float(q.x), -float(q.z), float(q.y), float(q.w))


def _bone_table(arm_obj):
    """([AnimBone] in file order, {bone_name: file_index})."""
    pairs = skeleton.bone_index_pairs(arm_obj)
    index_by_name = {bone.name: index for index, bone in pairs}
    anim_bones = []
    for index, bone in pairs:
        parent = -1
        if bone.parent is not None:
            parent = index_by_name.get(bone.parent.name, -1)
            if parent >= index:
                raise AnimExportError(
                    f"Bone '{bone.name}' (index {index}) has parent "
                    f"'{bone.parent.name}' (index {parent}); parents must "
                    "come first. Fix the rmv2_bone_index custom properties")
        anim_bones.append(af.AnimBone(name=bone.name, parent=parent))
    return anim_bones, index_by_name


def _unfix_matrix(local, bone):
    """Undo the cosmetic per-bone rest-orientation fix build_armature
    stamped on import (see skeleton.bone_fix_quaternion), turning a
    Blender-space parent-relative matrix back into the file-space one.
    A no-op for armatures without stored fixes (old files, hand-made
    armatures)."""
    fix_c = skeleton.bone_fix_quaternion(bone).to_matrix().to_4x4()
    fix_p = (skeleton.bone_fix_quaternion(bone.parent).to_matrix().to_4x4()
            if bone.parent is not None else Matrix.Identity(4))
    return fix_p @ local @ fix_c.inverted()


def _rest_local_matrices(arm_obj, pairs):
    out = []
    for _, bone in pairs:
        if bone.parent is not None:
            local = bone.parent.matrix_local.inverted() @ bone.matrix_local
        else:
            local = bone.matrix_local.copy()
        out.append(_unfix_matrix(local, bone))
    return out


def _sample_pose(arm_obj, pairs):
    """Parent-relative loc/quat of every pose bone at the current frame,
    in file order."""
    locs = []
    quats = []
    for _, bone in pairs:
        pb = arm_obj.pose.bones[bone.name]
        if pb.parent is not None:
            local = pb.parent.matrix.inverted() @ pb.matrix
        else:
            local = pb.matrix.copy()
        local = _unfix_matrix(local, bone)
        loc, quat, _scale = local.decompose()
        locs.append(loc)
        quats.append(quat)
    return locs, quats


def export_file(context, filepath: str, options: dict):
    """Export to filepath. Returns (stats, warnings)."""
    warnings: list[str] = []
    arm_obj = skeleton.find_context_armature(context)
    if arm_obj is None:
        raise AnimExportError("Select an armature to export a .anim")

    version = int(options.get("version", "7"))
    mode = options.get("mode", "ANIMATION")
    scale = options.get("global_scale", 1.0)

    arm_settings = arm_obj.data.rmv2
    skeleton_name = options.get("skeleton_name") \
        or arm_settings.skeleton_name \
        or arm_obj.name
    stored_flags = [f.strip() for f in arm_settings.flags.split(",")
                    if f.strip()]
    flags = stored_flags if version > 6 else []

    scene = context.scene
    frame_rate = float(options.get("frame_rate", 0.0)) \
        or arm_settings.anim_fps \
        or scene.render.fps / scene.render.fps_base

    pairs = skeleton.bone_index_pairs(arm_obj)
    anim_bones, _ = _bone_table(arm_obj)
    bone_count = len(anim_bones)
    if bone_count == 0:
        raise AnimExportError(f"Armature '{arm_obj.name}' has no bones")

    frames_loc = []
    frames_quat = []
    duration = None
    if mode == "BINDPOSE":
        # Vanilla skeleton files store the bind pose as two identical
        # frames with a 0.1 s duration; match that layout exactly.
        rest = _rest_local_matrices(arm_obj, pairs)
        locs = [m.to_translation() for m in rest]
        quats = [m.to_quaternion() for m in rest]
        frames_loc = [locs, locs]
        frames_quat = [quats, quats]
        duration = len(frames_loc) / frame_rate
    else:
        frame_start = int(options.get("frame_start", scene.frame_start))
        frame_end = int(options.get("frame_end", scene.frame_end))
        if frame_end < frame_start:
            raise AnimExportError(
                f"Empty frame range {frame_start}..{frame_end}")
        current = scene.frame_current
        try:
            depsgraph = context.evaluated_depsgraph_get()
            arm_eval = arm_obj.evaluated_get(depsgraph)
            for frame in range(frame_start, frame_end + 1):
                scene.frame_set(frame)
                locs, quats = _sample_pose(arm_eval, pairs)
                frames_loc.append(locs)
                frames_quat.append(quats)
        finally:
            scene.frame_set(current)

    num_frames = len(frames_loc)
    translations = np.zeros((num_frames, bone_count, 3), np.float32)
    rotations = np.zeros((num_frames, bone_count, 4), np.float32)
    for f in range(num_frames):
        for b in range(bone_count):
            loc = frames_loc[f][b]
            translations[f, b] = blender_to_game_translation(
                (loc[0] * scale, loc[1] * scale, loc[2] * scale))
            rotations[f, b] = blender_to_game_quaternion_xyzw(
                frames_quat[f][b])

    anim = af.build_simple(
        version=version, skeleton_name=skeleton_name,
        frame_rate=frame_rate, bones=anim_bones,
        translations=translations, rotations=rotations, flags=flags,
        duration=duration)

    blob = af.save(anim)
    with open(filepath, "wb") as handle:
        handle.write(blob)

    stats = {
        "bones": bone_count,
        "frames": num_frames,
        "skeleton_name": skeleton_name,
        "bytes": len(blob),
    }
    return stats, warnings
