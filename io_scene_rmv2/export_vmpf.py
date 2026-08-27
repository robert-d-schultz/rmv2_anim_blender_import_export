"""Exporter: Blender objects -> Shogun 2 .variant_part_mesh.

The inverse of import_vmpf.  Objects are gathered as a LOD ladder exactly
like the .rigid_model_v2 exporter does, and each vertex is written with up
to two weighted bone influences taken from its vertex groups.

The format stores no model-space position: every vertex holds its
position once per influence, in that influence's own bone space.  So the
armature is not optional here - without a bind pose there is nowhere to
put the vertices, and the export is refused rather than guessed at.

Deriving the two stored copies is exact, not a fit: for a vertex at model
-space point `p` influenced by bones a and b,

    position0 = bind[a]^-1 @ p        position1 = bind[b]^-1 @ p

and the tangent frame is rotated into each space the same way.  The
dominant influence goes in slot 0, which is what vanilla files do (the
weight byte is >= 128 in 99.8% of vanilla vertices).
"""

from __future__ import annotations

import json

import bpy
import numpy as np

from . import export_rmv2, scene_layout, skeleton, utils
from . import vmpf_format as vf
from .properties import (chosen_version, read_material_names,
                         root_format_version,
                         shader_params_or_default)


class VmpfExportError(Exception):
    pass


def _part_metadata(obj) -> dict:
    """What import_vmpf stashed for a library part, or {}."""
    raw = obj.rmv2.extra_json
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if "vmpf_part_name" in data else {}


def _part_name_for(obj, level: int) -> str:
    """The name to write for this object.

    The name it was imported with wins, so a untouched library re-exports
    unchanged. Anything new is named from the object, with the LOD number
    CA uses (their ladders start at lod1).
    """
    stored = _part_metadata(obj).get("vmpf_part_name")
    if stored:
        return stored
    stem = utils.strip_blender_suffix(obj.name)
    match = scene_layout.LOD_SUFFIX.match(stem)
    if match:
        stem = match.group(1)
    return f"{stem}_lod{level + 1}"


def _bone_index_for(obj, meta: dict) -> int:
    """The bone a library part rides.

    Prefers the live Child Of constraint the importer binds it with, so
    re-hanging a prop off a different bone in Blender is what gets
    written; falls back to the number stashed at import time for objects
    that were never attached (no armature in the scene), and to -1
    ("no mount") for anything hand-made.
    """
    for con in obj.constraints:
        if (con.type == "CHILD_OF" and con.target is not None
                and con.target.type == "ARMATURE" and con.subtarget):
            index = skeleton.bone_index_by_name(con.target).get(
                con.subtarget)
            if index is not None:
                return index
    stored = obj.rmv2.matrix_index
    if stored >= 0:
        return stored
    return int(meta.get("vmpf_bone_index", -1))


def _is_library(objects) -> bool:
    """Whether these objects form a library rather than one model.

    A library is many unrelated props in one file. Being imported from
    one settles it; otherwise more than one distinct prop name does.
    """
    if any(_part_metadata(obj) for obj in objects):
        return True
    stems = set()
    for obj in objects:
        stem = utils.strip_blender_suffix(obj.name)
        match = scene_layout.LOD_SUFFIX.match(stem)
        stems.add(match.group(1) if match else stem)
    return len(stems) > 1


# Welding precision.  The rigid layout stores float32 positions and UVs,
# so it is keyed as finely as .animatable_rigid_model; the skinned one
# stores half floats and there is no point keying finer than those.
_RIGID_POSITION_GRID = 1e5
_RIGID_UV_GRID = 1e5
_SKINNED_POSITION_GRID = 2048.0
_SKINNED_UV_GRID = 2048.0


def _root_for(objects):
    """The RMV2 root collection the exported objects belong to.

    gather_lods only reports a root when the user has made it the active
    collection; exporting from a plain selection leaves it None, and the
    skeleton name lives on that root - so find it from the objects, which
    sit either directly in a root or one level down in its LOD child.
    """
    for obj in objects:
        for col in obj.users_collection:
            if col.rmv2.is_rmv2_root:
                return col
            for candidate in bpy.data.collections:
                if candidate.rmv2.is_rmv2_root and col in set(
                        candidate.children):
                    return candidate
    return None


def _gather_objects(context, options: dict):
    """[(lod_level, [objects], decimate_ratio)], most detailed first.

    The ratio is 1.0 for a ladder the user set up by hand, and 0.5^level
    (or whatever the root's override rows say) when Generate LODs is on:
    this format's parts *are* its LOD ladder, exactly like .rigid_model_v2
    's LOD table, so the same decimator drives both.
    """
    root, lods = export_rmv2.gather_lods(context, options)
    if not lods:
        objects = [o for o in context.selected_objects if o.type == "MESH"]
        root = root or _root_for(objects)
        return root, ([(0, objects, 1.0)] if objects else [])

    if root is None:
        root = _root_for([o for objs in lods for o in objs[1]])
    if options.get("auto_lods", False):
        best = min(lods, key=lambda item: item[0]["level"])
        overrides = root.rmv2.lod_overrides if root is not None else None
        lods = export_rmv2.expand_auto_lods(
            [best], max(2, options.get("auto_lod_count", 4)), overrides)

    out = [(info["level"], list(objects), info.get("decimate_ratio", 1.0))
           for info, objects in lods]
    out.sort(key=lambda item: item[0])
    return root, out


def _use_rigid(armature, objects) -> bool:
    """Whether to write the rigid (stride 64) vertex format.

    A file has one layout for all its parts, and the objects carry the
    one they were imported with (RMV2 Object panel > Vertex Format), so
    the meshes decide rather than an export option - the same way the
    .rigid_model_v2 exporter works.  Any object explicitly asking for the
    skinned layout wins, since that is the one with more in it; if they
    all say rigid, rigid; and 'Auto' falls back to whether the meshes are
    actually weighted to anything.
    """
    settings = [obj.rmv2.vertex_format for obj in objects]
    if "VMPF_SKINNED" in settings:
        return False
    if settings and all(s == "VMPF_RIGID" for s in settings):
        return True
    if armature is None:
        return True
    return not any(obj.vertex_groups for obj in objects)


def _bone_local(frames: dict, bone: int, points: np.ndarray,
                directions: bool = False) -> np.ndarray:
    """Transform model-space rows into one bone's space."""
    matrix = frames[bone]
    rot_t = matrix[:3, :3]                  # rows are the bone's axes
    if directions:
        return points @ rot_t
    return (points - matrix[:3, 3]) @ rot_t


def _build_part(context, obj, options, frames, warnings, rigid: bool,
                decimate_ratio: float = 1.0):
    """One Blender object -> one VmpfPart.

    `decimate_ratio` below 1 has extract_mesh_arrays run a temporary
    Decimate modifier over the mesh, which is how a generated LOD ladder
    gets its coarser levels.
    """
    scale = options.get("global_scale", 1.0)
    # VMPF meshes are parented to the armature with an inverse that
    # cancels it, so the object's own local matrix is the model-space
    # placement; its translation belongs in the vertices because the
    # format has no pivot field (same situation as Shogun 2's
    # .rigid_model_v2 - see export_rmv2.extract_mesh_arrays).
    arrays = export_rmv2.extract_mesh_arrays(
        context, obj, dict(options, bone_local_space=True), warnings,
        decimate_ratio)
    if arrays is None:
        warnings.append(f"{obj.name}: no exportable faces; skipped")
        return None
    try:
        if rigid:
            welded = _weld_rigid(obj, arrays, scale)
            raw = _encode_rigid(welded)
        else:
            welded, bones, weights = _weld_skinned(
                obj, arrays, scale, frames, warnings)
            raw = _encode_skinned(welded, bones, weights, frames)

        if welded["count"] > 0xFFFF:
            raise VmpfExportError(
                f"{obj.name}: {welded['count']} vertices after welding, "
                "but the format's indices are 16-bit (65535 max). Split "
                "the mesh or reduce it")
        return vf.VmpfPart(
            vertices=raw,
            indices=welded["indices"].astype(np.uint16))
    finally:
        arrays["source_object"].to_mesh_clear()


def _weld_rigid(obj, arrays, scale):
    """weld_loops with the rigid layout's precision.

    extract_mesh_arrays substitutes opaque black when a mesh has no
    colour attribute; vanilla files store white and the shader multiplies
    by it, so black would darken the model (same reasoning as
    export_arm._build_mesh).
    """
    colours = (arrays["colours"] if obj.data.color_attributes
               else np.ones((len(arrays["loop_vidx"]), 4), np.float32))
    return export_rmv2.weld_loops(
        arrays, scale, _RIGID_POSITION_GRID, _RIGID_UV_GRID,
        colours=colours)


def _encode_rigid(welded):
    """Stride-64 vertex block: float positions, no skinning."""
    return vf.encode_rigid_vertices(
        welded["positions"], welded["normals"], welded["tangents"],
        welded["binormals"], welded["uv0"], welded["uv1"],
        welded["colours"])


def _weld_skinned(obj, arrays, scale, frames, warnings):
    """weld_loops plus the bone/weight columns the skinned format needs."""
    bone_map = export_rmv2.resolve_bone_map(obj, {}, warnings)
    if not bone_map:
        raise VmpfExportError(
            f"{obj.name}: no vertex group maps to a bone, so there is no "
            "bone space to store its vertices in. Attach the mesh to the "
            "model's armature first")
    vert_bone, vert_weight = export_rmv2._gather_weights(
        arrays["mesh_for_weights"], bone_map, 2, obj.name, warnings)

    missing = sorted({int(b) for b in np.unique(vert_bone)
                      if int(b) not in frames})
    if missing:
        raise VmpfExportError(
            f"{obj.name}: the armature has no bone for index/indices "
            + ", ".join(str(b) for b in missing[:6])
            + ("..." if len(missing) > 6 else ""))

    # Two loops at the same place with different influences are genuinely
    # different vertices to this format, so they join the weld key.
    loop_vidx = arrays["loop_vidx"]
    welded = export_rmv2.weld_loops(
        arrays, scale, _SKINNED_POSITION_GRID, _SKINNED_UV_GRID,
        extra_keys=(vert_bone[loop_vidx].astype(np.float64),
                    np.round(vert_weight[loop_vidx] * 255.0)))

    bones = vert_bone[welded["source_vertex"]].astype(np.int32)
    weights = vert_weight[welded["source_vertex"]].astype(np.float64)
    # The dominant influence is stored first (vanilla files put the weight
    # byte at >= 128 in 99.8% of vertices).
    swap = weights[:, 1] > weights[:, 0]
    bones[swap] = bones[swap][:, ::-1]
    weights[swap] = weights[swap][:, ::-1]
    total = weights.sum(axis=1)
    total[total <= 0.0] = 1.0
    weights = weights / total[:, None]
    return welded, bones, weights


def _encode_skinned(welded, bones, weights, frames):
    """Stride-48 vertex block: each position stored once per influence,
    in that influence's own bone space."""
    count = welded["count"]
    raw = np.zeros((count, 48), np.uint8)
    halfs = raw.view(np.float16).reshape(count, 24)
    two = weights[:, 1] > 0.0

    for slot in (0, 1):
        rows = np.nonzero(two if slot else np.ones(count, bool))[0]
        if not len(rows):
            continue
        for bone in np.unique(bones[rows, slot]):
            sel = rows[bones[rows, slot] == bone]
            bone = int(bone)
            local = _bone_local(frames, bone, welded["positions"][sel])
            normal = _bone_local(frames, bone, welded["normals"][sel], True)
            tangent = _bone_local(frames, bone, welded["tangents"][sel], True)
            binormal = _bone_local(frames, bone, welded["binormals"][sel],
                                   True)
            if slot == 0:
                halfs[sel, 0:3] = local.astype(np.float16)
                raw[sel, 24:27] = vf.encode_direction(normal)
                raw[sel, 32:35] = vf.encode_direction(tangent)
                raw[sel, 36:39] = vf.encode_direction(binormal)
            else:
                halfs[sel, 4:7] = local.astype(np.float16)
                raw[sel, 28:31] = vf.encode_direction(normal)
                raw[sel, 40:43] = vf.encode_direction(tangent)
                raw[sel, 44:47] = vf.encode_direction(binormal)

    halfs[:, 3] = welded["uv0"][:, 0].astype(np.float16)
    halfs[:, 7] = welded["uv0"][:, 1].astype(np.float16)
    halfs[:, 8:12] = np.array([0.0, 0.0, 1.0, 1.0], np.float16)
    raw[:, 27] = bones[:, 0].astype(np.uint8)
    raw[:, 31] = np.where(two, bones[:, 1], 0).astype(np.uint8)
    raw[:, 35] = np.round(np.where(two, weights[:, 0], 1.0)
                          * 255.0).astype(np.uint8)
    return raw


def export_file(context, filepath: str, options: dict):
    """Export to filepath. Returns (stats, warnings)."""
    warnings: list = []
    root, lods = _gather_objects(context, options)
    if not lods:
        raise VmpfExportError(
            "Nothing to export: select the mesh objects, or make the "
            "model's collection active")

    armature = skeleton.find_context_armature(context)
    if armature is None and root is not None:
        for obj in root.all_objects:
            if obj.type == "ARMATURE":
                armature = obj
                break
    every_object = [o for _, objects, _ in lods for o in objects]
    rigid = _use_rigid(armature, every_object)
    library = rigid and _is_library(every_object)
    if not rigid and armature is None:
        raise VmpfExportError(
            "The skinned vertex format stores every vertex in its bone's "
            "space, so an armature is required. Import the model's "
            "reference .anim and attach the meshes to it, then export - "
            "or set the meshes' Vertex Format to Variant Part Rigid if "
            "they are unskinned props")

    # Scaled, like every other caller: welded positions are multiplied
    # by global_scale, and _bone_local subtracts a bone origin from them.
    # An unscaled bind pose there put every skinned vertex somewhere
    # wrong at any scale but 1.
    frames = (skeleton.bind_frames_in_game_space(
        armature, options.get("global_scale", 1.0)) if armature else {})

    model = vf.VmpfFile(
        version=int(chosen_version(
            options, "version", root_format_version(root, "VMPF", "3"))),
        vertex_format=(vf.VF_RIGID_NAMED if library else
                       vf.VF_RIGID if rigid else vf.VF_SKINNED))
    # A library names its skeleton too - the props hang off its bones.
    model.skeleton_name = "" if (rigid and not library) else (
        options.get("skeleton_name", "")
        or (root.rmv2.skeleton_name if root is not None else ""))
    # A library names its materials per part, so the file-level list
    # stays empty there; everything else names them once, in the trailer.
    if root is not None and root.rmv2.material_names_initialized:
        model.material_names = read_material_names(root.rmv2)
    else:
        model.material_names = [] if library else ["default"] * 3
    # File-wide named parameters, kept from the import. Unlike
    # .variant_weighted_mesh there is no default to fall back on: every
    # vanilla .variant_part_mesh read so far carries an empty block, so a
    # model built in Blender gets one too rather than a borrowed set.
    if root is not None:
        model.float_params, model.vec4_params = shader_params_or_default(
            root.rmv2, ((), ()))

    if library and options.get("auto_lods", False):
        raise VmpfExportError(
            "Generate LODs cannot expand a library file: every part is "
            "named in the file and each prop has its own ladder, so a "
            "generated level would write the same stored name twice. "
            "Set the ladder up as LOD collections instead")
    if library:
        _build_library(context, lods, options, frames, warnings, model)
    else:
        for _, objects, ratio in lods:
            parts = []
            for obj in sorted(objects, key=lambda o: o.name):
                part = _build_part(context, obj, options, frames, warnings,
                                   rigid, ratio)
                if part is not None:
                    parts.append(part)
            if not parts:
                continue
            if len(parts) > 1:
                warnings.append(
                    f"{len(parts)} objects in one LOD were merged: the "
                    "format stores a single mesh per level")
                parts = [_merge(parts)]
            model.parts.append(parts[0])

    if not model.parts:
        raise VmpfExportError("No exportable meshes")

    blob = vf.save(model)
    with open(filepath, "wb") as handle:
        handle.write(blob)

    stats = {
        "lods": len(model.parts),
        "meshes": len(model.parts),
        "vertices": sum(p.vertex_count for p in model.parts),
        "triangles": sum(len(p.indices) // 3 for p in model.parts),
        "bytes": len(blob),
    }
    return stats, warnings


def _build_library(context, lods, options, frames, warnings, model) -> None:
    """Write every object as its own named part (vertex format 2).

    Unlike a LOD ladder, nothing is merged: a library is many separate
    props and each keeps its own entry, its own name and its own
    attachment slot.  The original file order is preserved where it is
    known, because vanilla libraries do not always keep one prop's LODs
    contiguous and reshuffling them would be a needless diff.
    """
    entries = []
    for level, objects, _ in lods:
        for obj in objects:
            entries.append((level, obj))

    def order(item):
        stored = _part_metadata(item[1]).get("vmpf_part_index")
        # Objects the user added have no original position; they go last,
        # in a stable order of their own.
        return (0, stored) if stored is not None else (1, item[1].name)

    entries.sort(key=order)

    for level, obj in entries:
        part = _build_part(context, obj, options, frames, warnings, True)
        if part is None:
            continue
        meta = _part_metadata(obj)
        part.name = _part_name_for(obj, level)
        part.bone_index = _bone_index_for(obj, meta)
        if obj.rmv2.material_names_initialized:
            part.material_names = read_material_names(obj.rmv2)
        else:
            names = list(meta.get("vmpf_material_names") or [])
            part.material_names = (names + ["default"] * 3)[:3]
        model.parts.append(part)


def _merge(parts: list) -> vf.VmpfPart:
    """Concatenate parts into one, offsetting each block's indices."""
    vertices = np.concatenate([p.vertices for p in parts], axis=0)
    indices = []
    offset = 0
    for part in parts:
        indices.append(np.asarray(part.indices, np.int64) + offset)
        offset += part.vertex_count
    joined = np.concatenate(indices) if indices else np.zeros(0, np.int64)
    if offset > 0xFFFF:
        raise VmpfExportError(
            f"merged LOD has {offset} vertices, over the format's 16-bit "
            "index limit (65535)")
    return vf.VmpfPart(vertices=vertices,
                       indices=joined.astype(np.uint16))
