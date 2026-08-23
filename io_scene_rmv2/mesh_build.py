"""Turning decoded geometry into a Blender mesh.

Shared by all three mesh importers (.rigid_model_v2,
.animatable_rigid_model / .rigid_model, .variant_part_mesh).  They read
very different files but end up needing the same six things: a mesh from
positions and triangles, UV layers, a colour attribute, custom split
normals and vertex groups.

Everything here takes data that is already in Blender space - the
game->Blender conversion, the winding reversal and the UV flip belong to
the caller (see utils.py), because only the caller knows which of them
its format needs.

Two ordering rules are load-bearing and easy to get wrong:

* `validate()` runs before any attribute layer is written, never after.
  It can drop faces, and a custom-normal layer left over from the old
  topology is inconsistent with the new one - which crashes Blender
  outright the next time the mesh is evaluated.
* per-loop attributes are gathered through the loop->vertex mapping read
  back *after* validate, so they stay correct even when faces were
  dropped.
"""

from __future__ import annotations

import bpy
import numpy as np

from . import skeleton, utils


class MeshBuildError(Exception):
    """Geometry that cannot be turned into a mesh at all (out-of-range
    indices).  Importers re-raise this as their own error type."""


def build_mesh(name: str, positions_b: np.ndarray, tris: np.ndarray,
               warnings: list | None = None):
    """Create a mesh from Blender-space positions and a triangle list.

    Returns (mesh, loop_vidx), where `loop_vidx` is the loop->vertex map
    every per-loop attribute has to be gathered through.  Index counts
    that are not a multiple of three are trimmed with a warning; indices
    out of range raise, since that means the file was misread.
    """
    tris = np.asarray(tris, np.int64)
    if tris.ndim == 1:
        usable = (len(tris) // 3) * 3
        if usable != len(tris) and warnings is not None:
            warnings.append(
                f"{name}: index count {len(tris)} is not a multiple of 3; "
                "ignoring the remainder")
        tris = tris[:usable]
    tris = tris.reshape(-1, 3)

    positions_b = np.ascontiguousarray(positions_b, np.float32)
    count = len(positions_b)
    if len(tris) and int(tris.max()) >= count:
        raise MeshBuildError(
            f"{name}: index {int(tris.max())} out of range for "
            f"{count} vertices")

    me = bpy.data.meshes.new(name)
    me.vertices.add(count)
    me.vertices.foreach_set("co", positions_b.ravel())

    nloops = tris.size
    me.loops.add(nloops)
    me.loops.foreach_set("vertex_index",
                         np.ascontiguousarray(tris, np.int32).ravel())
    me.polygons.add(len(tris))
    me.polygons.foreach_set("loop_start",
                            np.arange(0, nloops, 3, dtype=np.int32))
    me.validate(verbose=False, clean_customdata=False)

    if warnings is not None and len(me.polygons) != len(tris):
        warnings.append(
            f"{name}: Blender dropped "
            f"{len(tris) - len(me.polygons)} degenerate/duplicate face(s)")

    if len(me.polygons):
        me.polygons.foreach_set("use_smooth",
                                np.ones(len(me.polygons), np.int8))

    loop_vidx = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", loop_vidx)
    return me, loop_vidx


def add_uv_layer(me, name: str, per_vertex_uv: np.ndarray,
                 loop_vidx: np.ndarray) -> None:
    """One UV layer, gathered per loop.  `per_vertex_uv` is already
    flipped into Blender's bottom-left origin (utils.flip_uv_v)."""
    layer = me.uv_layers.new(name=name, do_init=False)
    if layer is None:                   # exceeded Blender's max UV layers
        return
    layer.data.foreach_set(
        "uv", per_vertex_uv[loop_vidx].ravel().astype(np.float32))


def add_colour_attribute(me, colours: np.ndarray, name: str = "Colour"):
    """Per-vertex colour as BYTE_COLOR, which is what every one of these
    formats actually stores.  Values are the file's raw bytes over 255,
    so they go in through `color_srgb`; export_rmv2._get_colours reads
    them back the same way."""
    ca = me.color_attributes.new(name=name, type="BYTE_COLOR",
                                 domain="POINT")
    flat = np.clip(colours, 0.0, 1.0).ravel().astype(np.float32)
    try:
        ca.data.foreach_set("color_srgb", flat)
    except (AttributeError, TypeError):
        ca.data.foreach_set("color", flat)
    return ca


def set_custom_normals(me, normals_b: np.ndarray) -> None:
    """Custom split normals from per-vertex normals, skipped when the
    file had none to give (an all-zero block)."""
    if not len(normals_b) or np.allclose(normals_b, 0.0):
        return
    normals = utils.normalize_rows(normals_b, fallback=(0.0, 0.0, 0.0))
    if hasattr(me, "use_auto_smooth"):      # Blender <= 4.0
        me.use_auto_smooth = True
    me.normals_split_custom_set_from_vertices(normals.tolist())


def add_vertex_groups(obj, indices: np.ndarray, weights: np.ndarray,
                      bone_names: dict) -> None:
    """One group per referenced bone, weights summed per vertex.

    `indices` and `weights` are (vertex count, influences).  A vertex can
    name the same bone in more than one influence slot, so the
    contributions are added rather than the later one overwriting the
    earlier.
    """
    indices = np.asarray(indices)
    weights = np.asarray(weights, np.float32)
    if indices.ndim != 2 or not indices.shape[1] or not len(indices):
        return
    for bone in np.unique(indices[weights > 0.0]).tolist():
        summed = np.where(indices == bone, weights, 0.0).sum(axis=1)
        verts = np.nonzero(summed > 0.0)[0]
        if not len(verts):
            continue
        group = obj.vertex_groups.new(
            name=bone_names.get(bone, skeleton.fallback_bone_name(bone)))
        values = summed[verts]
        # Weights are byte-quantized, so batching equal values is cheap.
        for value in np.unique(values):
            rows = verts[values == value]
            group.add(rows.tolist(), float(value), "REPLACE")
