"""Shared helpers for the RMV2 add-on.

Coordinate convention
---------------------
RMV2 stores data in the game's space: right-handed, Y-up (this is how
AssetEditor's MonoGame renderer consumes it verbatim).

Blender is right-handed, Z-up, so:

    blender (x, y, z) = (game.x, -game.z, game.y)      # +90deg around X
    game    (x, y, z) = (blender.x, blender.z, -blender.y)

This is a pure rotation (determinant +1), so it preserves orientation:
cross products/winding carry straight through unchanged. Real RMV2 files
confirm this empirically (checked against their own stored per-vertex
normals across multiple assets) - front-facing triangles use the same
winding, in either space, as Blender's counter-clockwise convention, so
import/export must NOT reverse triangle winding. `reverse_winding` is
kept only for the export-side case of an object with a mirrored
(negative-determinant) transform, whose baked-in reflection flips
orientation once and needs exactly one compensating reversal.

This module has no bpy dependency (numpy only) so it stays testable.
"""

from __future__ import annotations

import numpy as np

_G2B_SIGN = np.array([1.0, -1.0, 1.0], dtype=np.float32)
_B2G_SIGN = np.array([1.0, 1.0, -1.0], dtype=np.float32)


def game_to_blender(arr: np.ndarray) -> np.ndarray:
    """(n,3) game-space -> (n,3) Blender-space (positions or directions)."""
    a = np.asarray(arr, dtype=np.float32)
    return a[:, (0, 2, 1)] * _G2B_SIGN


def blender_to_game(arr: np.ndarray) -> np.ndarray:
    """(n,3) Blender-space -> (n,3) game-space (positions or directions)."""
    a = np.asarray(arr, dtype=np.float32)
    return a[:, (0, 2, 1)] * _B2G_SIGN


def game_to_blender_v(v) -> tuple:
    """Single vector variant."""
    return (float(v[0]), -float(v[2]), float(v[1]))


def blender_to_game_v(v) -> tuple:
    return (float(v[0]), float(v[2]), -float(v[1]))


def flip_uv_v(uv: np.ndarray) -> np.ndarray:
    """DirectX UV origin is top-left, Blender's is bottom-left.
    The mapping v' = 1 - v is its own inverse."""
    out = np.asarray(uv, dtype=np.float32).copy()
    out[:, 1] = 1.0 - out[:, 1]
    return out


def reverse_winding(tris: np.ndarray) -> np.ndarray:
    """(n,3) triangle index array with winding reversed."""
    return np.ascontiguousarray(tris[:, ::-1])


def normalize_rows(arr: np.ndarray, fallback=(0.0, 0.0, 1.0)) -> np.ndarray:
    """Normalize (n,3) rows; zero-length rows become `fallback`."""
    a = np.asarray(arr, dtype=np.float32)
    lengths = np.linalg.norm(a, axis=1)
    bad = lengths < 1e-12
    lengths[bad] = 1.0
    out = a / lengths[:, None]
    out[bad] = np.asarray(fallback, dtype=np.float32)
    return out


def strip_blender_suffix(name: str) -> str:
    """Remove Blender's numeric duplicate suffix ('body.001' -> 'body')."""
    if len(name) > 4 and name[-4] == "." and name[-3:].isdigit():
        return name[:-4]
    return name
