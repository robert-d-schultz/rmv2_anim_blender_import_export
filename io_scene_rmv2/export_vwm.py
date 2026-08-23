"""Exporter: Blender objects -> Empire/Napoleon .variant_weighted_mesh.

The inverse of import_vwm.  One file is one LOD of one unit, so this
writes a single LOD level's objects, each object becoming one named part
in the file.  Which level is chosen is the caller's business (see the
`lod_level` option); the LOD ladder itself is four separate files.

Every vertex is written with one to eight weighted bone influences taken
from its vertex groups.  Since the format stores no model-space position
- each influence holds its own copy of the vertex in that bone's space -
the armature is not optional here: without a bind pose there is nowhere
to put the vertices, and the export is refused rather than guessed at.

Deriving the stored copies is exact, not a fit.  For a vertex at
model-space point `p` influenced by bones a, b, c ...:

    position[a] = bind[a]^-1 @ p,  position[b] = bind[b]^-1 @ p,  ...

and the normal is rotated into each bone's space the same way.  The
tangent and binormal are stored once, in the *first* influence's space,
which is where vanilla files keep them.  Influences are written
strongest first, as CA's own exporter does.

An object with no vertex groups is not a skinned part at all but an
*attachment* - a prop welded to one bone, written into the file's second
section as a rigid .animatable_rigid_model object.  That is the same
distinction the importer makes on the way in, and the same one the
format itself makes.
"""

from __future__ import annotations

import os

import numpy as np

from . import export_arm, export_rmv2, skeleton, utils
from . import vwm_format as vf

# The float32 layout resolves far more than this; these are the grids
# weld_loops keys on, matching the other float32 formats in the add-on.
_POSITION_GRID = 1e5
_UV_GRID = 1e5


class VwmExportError(Exception):
    pass


def _bone_local(frames: dict, bone: int, rows: np.ndarray,
                directions: bool = False) -> np.ndarray:
    """Transform model-space rows into one bone's space.

    `frames[bone]` is the bind matrix, whose rotation block is orthonormal
    (bone bind poses carry no scale), so its transpose is its inverse and
    `rows @ rot` is `rot^-1 @ rows` done row-wise.
    """
    matrix = frames[bone]
    rot = np.asarray(matrix, np.float32)[:3, :3]
    if directions:
        return rows @ rot
    return (rows - np.asarray(matrix, np.float32)[:3, 3]) @ rot


def _part_name_for(obj, base: str, index: int) -> str:
    """The name to write for this object.

    Blender's ".001" duplicate suffix is stripped - importing four LODs
    of one unit gives four objects called `<unit>_head01`, and only the
    first keeps the bare name.
    """
    name = utils.strip_blender_suffix(obj.name)
    return name or f"{base}_part{index:02d}"


def _build_part(context, obj, options, frames, warnings):
    """One Blender object -> one VwmPart, or None if it has no faces."""
    scale = options.get("global_scale", 1.0)
    # These meshes are parented to the armature with an inverse that
    # cancels it, so the object's own local matrix is its model-space
    # placement - and unlike .rigid_model_v2 there is no pivot field to
    # carry the translation, so it has to go into the vertices.  That is
    # exactly what bone_local_space asks extract_mesh_arrays for.
    arrays = export_rmv2.extract_mesh_arrays(
        context, obj, dict(options, bone_local_space=True), warnings, 1.0)
    if arrays is None:
        warnings.append(f"{obj.name}: no exportable faces; skipped")
        return None

    try:
        bone_map = export_rmv2.resolve_bone_map(obj, {}, warnings)
        if not bone_map:
            raise VwmExportError(
                f"{obj.name}: no vertex group maps to a bone, so there is "
                "no bone space to store its vertices in. Attach the mesh "
                "to the model's armature first")

        vert_bone, vert_weight = export_rmv2._gather_weights(
            arrays["mesh_for_weights"], bone_map, vf.MAX_INFLUENCES,
            obj.name, warnings)

        missing = sorted({int(b) for b in np.unique(
            vert_bone[vert_weight > 0.0]) if int(b) not in frames})
        if missing:
            raise VwmExportError(
                f"{obj.name}: the armature has no bone for index/indices "
                + ", ".join(str(b) for b in missing[:6])
                + ("..." if len(missing) > 6 else ""))

        # Two loops at the same place with different influences are
        # genuinely different vertices to this format, so the whole
        # influence table joins the weld key.
        loop_vidx = arrays["loop_vidx"]
        welded = export_rmv2.weld_loops(
            arrays, scale, _POSITION_GRID, _UV_GRID,
            extra_keys=(vert_bone[loop_vidx].astype(np.float64),
                        np.round(vert_weight[loop_vidx] * 65535.0)))

        source = welded["source_vertex"]
        bones = vert_bone[source].astype(np.int64)
        weights = vert_weight[source].astype(np.float32)

        part = vf.VwmPart(name="")
        part.uv = welded["uv0"].astype(np.float32)
        part.indices = welded["indices"].astype(np.uint32)

        _fill_influences(part, welded, bones, weights, frames)
        return part
    finally:
        arrays["source_object"].to_mesh_clear()


def _fill_influences(part, welded, bones, weights, frames) -> None:
    """Write the per-influence, per-bone-space copies of every vertex."""
    count = len(welded["positions"])
    positions = welded["positions"]
    normals = welded["normals"]
    tangents = welded["tangents"]
    binormals = welded["binormals"]

    used = weights > 0.0
    # A vertex with no weight at all was assigned fully to bone 0 by
    # _gather_weights, which leaves its first slot the only real one.
    used[~used.any(axis=1), 0] = True
    counts = used.sum(axis=1).astype(np.int32)

    rows, slots = np.nonzero(used)
    order = np.lexsort((slots, rows))       # vertex order, slot order
    rows, slots = rows[order], slots[order]

    out_bones = bones[rows, slots].astype(np.uint32)
    out_weights = weights[rows, slots].astype(np.float32)
    out_positions = np.zeros((len(rows), 3), np.float32)
    out_normals = np.zeros((len(rows), 3), np.float32)

    for bone in np.unique(out_bones):
        pick = out_bones == bone
        source = rows[pick]
        out_positions[pick] = _bone_local(frames, int(bone),
                                          positions[source])
        out_normals[pick] = _bone_local(frames, int(bone), normals[source],
                                        directions=True)

    # Tangent and binormal are stored once per vertex, in the space of
    # its first influence.
    first_slot = np.concatenate(([0], np.cumsum(counts)[:-1]))
    first_bone = out_bones[first_slot]
    part_tangents = np.zeros((count, 3), np.float32)
    part_binormals = np.zeros((count, 3), np.float32)
    for bone in np.unique(first_bone):
        pick = first_bone == bone
        part_tangents[pick] = _bone_local(frames, int(bone),
                                          tangents[pick], directions=True)
        part_binormals[pick] = _bone_local(frames, int(bone),
                                           binormals[pick], directions=True)
    part.tangents = part_tangents
    part.binormals = part_binormals

    for key, value in vf.build_influences(
            counts, out_bones, out_positions, out_normals,
            out_weights).items():
        setattr(part, key, value)


def build_model(context, objects, base: str, options, warnings):
    """Turn one LOD's objects into a VwmFile."""
    armature = options.get("armature")
    if armature is None:
        armature = skeleton.find_context_armature(context)
    if armature is None:
        for obj in objects:
            for mod in obj.modifiers:
                if mod.type == "ARMATURE" and mod.object is not None:
                    armature = mod.object
                    break
            if armature is not None:
                break
    skinned = [obj for obj in objects if not _is_attachment(obj)]
    if armature is None and skinned:
        raise VwmExportError(
            "This format stores no model-space positions - every vertex "
            "is written once per bone that moves it, in that bone's space "
            "- so it cannot be exported without an armature. Select the "
            "model's armature, or attach the meshes to it, and try again")

    scale = options.get("global_scale", 1.0)
    # Attachments carry their bone index themselves, so a file of nothing
    # but props needs no bind pose.
    frames = ({} if armature is None
              else skeleton.bind_frames_in_game_space(armature, scale))

    model = vf.VwmFile(version=int(options.get("version", 1)))
    model.float_params = list(options.get("float_params") or
                              _DEFAULT_FLOAT_PARAMS)
    model.vec4_params = list(options.get("vec4_params") or
                             _DEFAULT_VEC4_PARAMS)

    for index, obj in enumerate(objects):
        if _is_attachment(obj):
            entry = _build_attachment(context, obj, options, armature,
                                      base, index, warnings)
            if entry is not None:
                model.attachments.append(entry)
            continue
        part = _build_part(context, obj, options, frames, warnings)
        if part is None:
            continue
        part.name = _part_name_for(obj, base, index)
        model.parts.append(part)

    if not model.parts and not model.attachments:
        raise VwmExportError("Nothing to export: no mesh objects with "
                             "faces were found")
    return model


def _is_attachment(obj) -> bool:
    """Whether this object is a prop on one bone rather than a skinned part.

    Skinned parts always come out of the importer with vertex groups,
    because that is what the influences become; an attachment never has
    any, and rides a bone via a Child Of constraint or matrix_index.
    """
    return not obj.vertex_groups


def _build_attachment(context, obj, options, armature, base: str,
                      index: int, warnings: list):
    """One Blender object -> one VwmAttachment.

    The geometry is an ordinary ARM object in its bone's space, so the
    ARM exporter builds it; only the name and bone index are this
    format's own.
    """
    mesh = export_arm._build_mesh(
        context, obj, dict(options, arm_version=_ATTACHMENT_VERSION),
        warnings)
    if mesh is None:
        return None
    bone = export_arm._bone_index(obj, armature, True)
    return vf.VwmAttachment(name=_part_name_for(obj, base, index),
                            bone=bone, mesh=mesh)


# Every vanilla attachment that is not from CA's testdata folder is an
# object version 5 one.
_ATTACHMENT_VERSION = 5


# The material parameter block every vanilla unit file carries.  The
# format has no texture slots - the part name selects the texture set -
# so these values are all a new file needs to look right in-game.
_DEFAULT_FLOAT_PARAMS = (
    ("light_scale", 1.0),
    ("offsetu0", 0.0),
    ("offsetv0", 0.0),
    ("offsetu1", 0.0),
    ("offsetv1", 0.0),
    ("bumpfactor", 1.0),
    ("specpower", 2.0),
    ("specbrightness", 1.0),
    ("specularfresnelpower", 0.01),
    ("glossfactor", 2.0),
    ("fresnelpower", 2.0),
    ("reflect_factor", 0.5),
    ("ambientfactor", 0.6),
)
_DEFAULT_VEC4_PARAMS = (
    ("colourmapfactor", (1.0, 1.0, 1.0, 0.0)),
    ("specfactor", (1.0, 1.0, 1.0, 1.0)),
)


def export_file(context, filepath: str, options: dict):
    """Write one .variant_weighted_mesh. Returns (stats, warnings)."""
    warnings: list = []
    root, lods = export_rmv2.gather_lods(context, options)

    wanted = int(options.get("lod_level", 0))
    objects = None
    for info, objs in lods:
        if info.get("level") == wanted:
            objects = objs
            break
    if objects is None and lods:
        objects = lods[0][1]
        warnings.append(
            f"No LOD {wanted} in this model; exported LOD "
            f"{lods[0][0].get('level', 0)} instead")
    if not objects:
        raise VwmExportError("Nothing to export: no mesh objects found")

    base = root.name if root is not None else os.path.splitext(
        os.path.basename(filepath))[0]
    model = build_model(context, objects, base, options, warnings)

    data = vf.save(model)
    # Re-read what we are about to write, so a file that would not load
    # back never reaches disk (same guard as the RMV2 writer).
    vf.load(data)
    with open(filepath, "wb") as handle:
        handle.write(data)

    stats = {
        "meshes": len(model.parts) + len(model.attachments),
        "attachments": len(model.attachments),
        "vertices": (sum(p.vertex_count for p in model.parts)
                     + sum(a.mesh.vertex_count for a in model.attachments)),
        "triangles": (sum(len(p.indices) // 3 for p in model.parts)
                      + sum(len(a.mesh.indices) // 3
                            for a in model.attachments)),
        "bytes": len(data),
    }
    return stats, warnings
