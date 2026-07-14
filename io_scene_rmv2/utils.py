"""Shared helpers for the RMV2 add-on.

Coordinate convention
---------------------
RMV2 stores data in the game's space: Y-up and effectively LEFT-handed.
The proof is in AssetEditor's own renderer (Camera.cs): it multiplies its
projection matrix by CreateScale(-1, 1, 1), mirroring the whole viewport
horizontally so the raw right-handed MonoGame render matches how the game
actually displays the data. A model imported into Blender (right-handed)
without a compensating reflection therefore appears mirrored compared to
the game - confirmed on real assets viewed side by side with Terry.

Blender is right-handed, Z-up, so the conversion carries exactly one
reflection (determinant -1):

    blender (x, y, z) = (-game.x, -game.z, game.y)
    game    (x, y, z) = (-blender.x, blender.z, -blender.y)

Negating X (rather than some other axis) makes AssetEditor's default view
correspond to Blender's front view: a game character facing game +Z faces
Blender -Y.

Consequences of the reflection:
- Triangle winding flips orientation once, so import AND export each
  reverse winding once (`reverse_winding`). On export this XORs with the
  compensation for an object's own mirrored (negative-determinant)
  transform: such objects need NO extra reversal.
- Stored per-vertex normals in real files agree with the CCW cross
  product of the raw game-space winding, so after the mapping + reversal
  Blender's geometric CCW normals agree with the imported split normals.
- Quaternions are conjugated by the reflection: the vector part maps
  through the linear map *negated* (pseudo-vector rule), w is unchanged.
  See import_anim/export_anim.

This module has no bpy dependency (numpy only) so it stays testable.
"""

from __future__ import annotations

import numpy as np

_G2B_SIGN = np.array([-1.0, -1.0, 1.0], dtype=np.float32)
_B2G_SIGN = np.array([-1.0, 1.0, -1.0], dtype=np.float32)


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
    return (-float(v[0]), -float(v[2]), float(v[1]))


def blender_to_game_v(v) -> tuple:
    return (-float(v[0]), float(v[2]), -float(v[1]))


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
