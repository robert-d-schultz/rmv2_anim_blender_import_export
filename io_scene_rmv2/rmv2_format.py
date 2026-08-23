"""Binary reader/writer for Creative Assembly's .rigid_model_v2 (RMV2) format.

This module is intentionally free of any Blender (bpy) dependencies so it can
be unit-tested with a plain Python interpreter.  All heavy lifting is done
with numpy (which Blender bundles).

The implementation follows the C# reference in
TheAssetEditor/Shared/GameFiles/RigidModel and matches its binary layout
byte-for-byte:

    RmvFileHeader           140 bytes   "RMV2", version, lod count, skeleton
    RmvLodHeader            20 (v5/v6) or 28 (v7/v8) bytes, lod_count times
    per mesh:
        RmvCommonHeader     80 bytes
        material header     variable (WeightedMaterial for almost everything)
        vertex data         vertex_count * stride
        index data          index_count * uint16

Those sizes are versions 6 to 8.  Rome 2 shipped with version 5, whose
every fixed-width string is UTF-16 and therefore twice as wide - a
268-byte file header, a 112-byte common header, a 1404-byte weighted
material, 116-byte attachment points and 516-byte texture entries.  The
fields themselves are the same ones in the same order, so the same
parsers cover both; see _wide_strings.

Versions 1 to 3 are older still and share Shogun 2's arrangement
entirely (see _load_shogun2): no skeleton name in the file header, a
512-byte UTF-16 one after the LOD table, a 48-byte common header, and a
material that is a run of fixed-width fields rather than a tagged list.
Rome 2 keeps version 3 for its 3D interface models.

All data is little-endian.  Coordinates are in the game's space
(right-handed, Y-up); conversion to Blender space happens elsewhere.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Enums (kept as plain ints + name tables so the module has no bpy/enum deps)
# ---------------------------------------------------------------------------

# Versions 1 to 3 have a different header, common-header and material
# layout to versions 5 and up - see _load_shogun2.  1 and 2 are Shogun 2;
# 3 is the same layout carried into Rome 2, which uses it for the 3D user
# interface models it inherited, and nothing else.
SHOGUN2_VERSIONS = (1, 2, 3)

SUPPORTED_VERSIONS = (1, 2, 3, 5, 6, 7, 8)

# VertexFormat
VF_STATIC = 0
VF_COLLISION = 1
VF_WEIGHTED = 3           # 2 bone influences
VF_CINEMATIC = 4          # 4 bone influences
VF_POSITION16 = 5
VF_CUSTOM_TERRAIN = 6
VF_CUSTOM_TERRAIN2 = 13

# Shogun 2 materials carry no vertex-format field, so these are not game
# enum values - they are our own ids for the layouts stride identifies.
# Numbered from 100 to stay clear of the real enum.
VF_POSITION_UV = 100      # stride 12: half4 position + half2 uv
VF_S2_POSITION_UV = VF_POSITION_UV      # its first use was Shogun 2's
VF_S2_STATIC_NO_UV2 = 101  # stride 28: Static without the second uv
VF_S2_STATIC_FLOAT = 102  # stride 44: Static with float32 position and uvs
VF_S2_BOW_WAVE = 103      # stride 24: two half4 positions + uv + a float

# Rome 2 uses two vertex-format ids this add-on has no other name for:
# 7 on tree billboards and 8 on water planes.  They are the file's own
# values, kept so a re-saved material declares what it declared before.
VF_ROME2_TREE = 7
VF_ROME2_WATER = 8

# Trees, shrubs and hedges declare vertex format 6 (CustomTerrain) but
# carry 60 bytes, not 36: a half4 tangent frame instead of a byte one, a
# second position, and eight trailing halves that drive the wind sway.
# The game tells them apart by material, so the only thing separating
# them in the file is the stride - hence another id of our own.
VF_VEGETATION = 104       # stride 60

# The flat billboard a tree collapses to at the furthest LOD: position,
# normal, uv and eight bytes that are zero in every vanilla mesh.
VF_TREE_BILLBOARD = 105   # stride 28

# Grass, which keeps its uvs as full float32 - the only layout that does.
VF_GRASS = 106            # stride 28

VERTEX_FORMAT_NAMES = {
    VF_STATIC: "Static",
    VF_COLLISION: "Collision",
    VF_WEIGHTED: "Weighted",
    VF_CINEMATIC: "Cinematic",
    VF_POSITION16: "Position16_bit",
    VF_CUSTOM_TERRAIN: "CustomTerrain",
    VF_CUSTOM_TERRAIN2: "CustomTerrain2",
    VF_POSITION_UV: "PositionUV",
    VF_S2_STATIC_NO_UV2: "Shogun2_Static",
    VF_S2_STATIC_FLOAT: "Shogun2_StaticFloat",
    VF_S2_BOW_WAVE: "Shogun2_BowWave",
    VF_VEGETATION: "Vegetation",
    VF_TREE_BILLBOARD: "TreeBillboard",
    VF_GRASS: "Grass",
    VF_ROME2_TREE: "Rome2_Tree",
    VF_ROME2_WATER: "Rome2_Water",
}

# ModelMaterialEnum (ushort in the file)
MATERIAL_NAMES = {
    22: "bow_wave",
    26: "non_renderable",
    29: "texture_combo_vertex_wind",
    30: "texture_combo",
    31: "decal_waterfall",
    32: "standard_simple",
    34: "campaign_trees",
    38: "point_light",
    45: "static_point_light",
    46: "debug_geometry",
    49: "custom_terrain",
    58: "weighted_cloth",
    60: "cloth",
    61: "collision",
    62: "collision_shape",
    63: "tiled_dirtmap",
    64: "ship_ambientmap",
    65: "weighted",
    67: "projected_decal",
    68: "default_type",
    69: "grass",
    70: "weighted_skin",
    71: "decal",
    72: "decal_dirtmap",
    73: "dirtmap",
    74: "tree",
    75: "tree_leaf",
    77: "weighted_decal",
    78: "weighted_decal_dirtmap",
    79: "weighted_dirtmap",
    80: "weighted_skin_decal",
    81: "weighted_skin_decal_dirtmap",
    82: "weighted_skin_dirtmap",
    83: "water",
    84: "unlit",
    85: "weighted_unlit",
    86: "terrain_blend",
    87: "projected_decal_v2",
    88: "ignore",
    89: "tree_billboard_material",
    91: "water_displace_volume",
    93: "rope",
    94: "campaign_vegetation",
    95: "projected_decal_v3",
    96: "weighted_texture_blend",
    97: "projected_decal_v4",
    98: "global_terrain",
    99: "decal_overlay",
    100: "alpha_blend",
    101: "TerrainTiles",
}
MATERIAL_IDS = {v: k for k, v in MATERIAL_NAMES.items()}

MAT_CUSTOM_TERRAIN = 49
MAT_TERRAIN_TILES = 101
MAT_WEIGHTED = 65
MAT_DEFAULT = 68

# TextureType
TEXTURE_TYPE_NAMES = {
    0: "Diffuse",
    1: "Normal",
    3: "Mask",
    5: "Ambient_occlusion",
    7: "Tiling_dirt_uv2",
    10: "Skin_mask",
    11: "Specular",
    12: "Gloss",
    13: "Decal_dirtmap",
    14: "Decal_dirtmask",
    15: "Decal_mask",
    17: "Diffuse_damage",
    27: "BaseColour",
    29: "MaterialMap",
}

TEXTURE_TYPE_DIFFUSE = 0
TEXTURE_TYPE_NORMAL = 1
TEXTURE_TYPE_AMBIENT_OCCLUSION = 5

# Well known material parameter slots (WeightedParamterIds in the C# code)
FLOAT_PARAM_UV_SCALE_X = 0
FLOAT_PARAM_UV_SCALE_Y = 1
INT_PARAM_ALPHA = 0
INT_PARAM_DECAL = 1
INT_PARAM_DIRT = 2
VEC4_PARAM_TEXTURE_DECAL_TRANSFORM = 0

ALPHA_MODE_OPAQUE = 0
ALPHA_MODE_TRANSPARENT = 1

# ---------------------------------------------------------------------------
# Struct layouts
# ---------------------------------------------------------------------------

_FILE_HEADER = struct.Struct("<4sII128s")                # 140
_LOD_HEADER_V5_V6 = struct.Struct("<IIIIf")              # 20
_LOD_HEADER_V7_V8 = struct.Struct("<IIIIfIBBBB")         # 28
_COMMON_HEADER = struct.Struct("<HHIIIII6f12s10s10s")    # 80
_WEIGHTED_MATERIAL = struct.Struct("<H32s256s256s2B3f36fii6I124s")  # 860
_ATTACHMENT_POINT = struct.Struct("<32s12fi")            # 84
_TEXTURE = struct.Struct("<i256s")                       # 260

# Rome 2's version 5 predates the switch to UTF-8: every fixed-width string
# field is UTF-16 and therefore twice as wide.  Field order and count are
# the same as v6, so the same parsers cover both - only the widths and the
# codec change.  See _wide_strings.
_FILE_HEADER_V5 = struct.Struct("<4sII256s")             # 268
_COMMON_HEADER_V5 = struct.Struct("<HHIIIII6f24s20s20s")  # 112
_WEIGHTED_MATERIAL_V5 = struct.Struct(
    "<H64s512s512s2B3f36fii6I124s")                      # 1404
_ATTACHMENT_POINT_V5 = struct.Struct("<64s12fi")         # 116
_TEXTURE_V5 = struct.Struct("<i512s")                    # 516
_TERRAIN_TILE_MATERIAL = struct.Struct("<64s6I")         # 88
_CUSTOM_TERRAIN_MATERIAL = struct.Struct("<256s")        # 256

# Shogun 2 (v1/v2).  The file header carries no name - the skeleton name
# is a 512-byte UTF-16 field *after* the lod table, preceded by a u32 that
# is 0 in every file seen so far.  The common header is the first 48 bytes
# of the modern one (the three shader-name fields do not exist yet).
_S2_FILE_HEADER = struct.Struct("<4sII")                 # 12
_S2_COMMON_HEADER = struct.Struct("<HHIIIII6f")          # 48
_S2_NAME_SIZE = 512                                      # 256 UTF-16 chars
_S2_STR32 = 64                                           # 32 UTF-16 chars
_S2_STR256 = 512                                         # 256 UTF-16 chars

assert _FILE_HEADER.size == 140
assert _COMMON_HEADER.size == 80
assert _S2_FILE_HEADER.size == 12
assert _S2_COMMON_HEADER.size == 48
assert _WEIGHTED_MATERIAL.size == 860
assert _ATTACHMENT_POINT.size == 84
assert _TEXTURE.size == 260
assert _FILE_HEADER_V5.size == 268
assert _COMMON_HEADER_V5.size == 112
assert _WEIGHTED_MATERIAL_V5.size == 1404
assert _ATTACHMENT_POINT_V5.size == 116
assert _TEXTURE_V5.size == 516


class RmvFormatError(Exception):
    """Raised when a file cannot be parsed/serialized."""


# ---------------------------------------------------------------------------
# String helpers (CA conventions)
# ---------------------------------------------------------------------------

def _decode_fixed_string(raw: bytes) -> str:
    """Zero padded fixed length string."""
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _encode_fixed_string(value: str, length: int) -> bytes:
    raw = value.encode("utf-8", errors="replace")[:length]
    return raw.ljust(length, b"\0")


def _decode_fixed_utf16(raw: bytes) -> str:
    """Zero padded fixed length UTF-16LE string (Shogun 2 convention)."""
    text = raw.decode("utf-16-le", errors="replace")
    return text.split("\0", 1)[0]


def _encode_fixed_utf16(value: str, length: int) -> bytes:
    raw = value.encode("utf-16-le", errors="replace")[:length]
    if len(raw) % 2:                    # never split a UTF-16 code unit
        raw = raw[:-1]
    return raw.ljust(length, b"\0")


def _wide_strings(version: int) -> bool:
    """Rome 2's v5 writes fixed strings as UTF-16 at double width."""
    return version == 5


def _decode_name(raw: bytes, wide: bool) -> str:
    return _decode_fixed_utf16(raw) if wide else _decode_fixed_string(raw)


def _encode_name(value: str, length: int, wide: bool) -> bytes:
    """Encode into a field `length` bytes wide in v6+, twice that in v5."""
    if wide:
        return _encode_fixed_utf16(value, length * 2)
    return _encode_fixed_string(value, length)


def _file_header_struct(version: int) -> struct.Struct:
    return _FILE_HEADER_V5 if _wide_strings(version) else _FILE_HEADER


def _common_header_struct(version: int) -> struct.Struct:
    return _COMMON_HEADER_V5 if _wide_strings(version) else _COMMON_HEADER


def _weighted_material_struct(version: int) -> struct.Struct:
    return (_WEIGHTED_MATERIAL_V5 if _wide_strings(version)
            else _WEIGHTED_MATERIAL)


def _attachment_point_struct(version: int) -> struct.Struct:
    return (_ATTACHMENT_POINT_V5 if _wide_strings(version)
            else _ATTACHMENT_POINT)


def _texture_struct(version: int) -> struct.Struct:
    return _TEXTURE_V5 if _wide_strings(version) else _TEXTURE


def _read_ca_string(buf: bytes, offset: int) -> tuple[str, int]:
    """u16 byte-length prefixed UTF-8 string. Returns (value, bytes_read)."""
    (length,) = struct.unpack_from("<H", buf, offset)
    start = offset + 2
    value = buf[start:start + length].decode("utf-8", errors="replace")
    return value, 2 + length


def _write_ca_string(value: str) -> bytes:
    raw = value.encode("utf-8", errors="replace")
    return struct.pack("<H", len(raw)) + raw


# ---------------------------------------------------------------------------
# Vertex packing helpers
# ---------------------------------------------------------------------------

def _decode_position_half4(raw: np.ndarray) -> np.ndarray:
    """(n,4) float16 -> (n,3) float32.

    CA stores an optional extra-precision scale in W: if W != 0 the actual
    position is XYZ * W.
    """
    p = raw.astype(np.float32)
    w = p[:, 3]
    scale = np.where(w != 0.0, w, 1.0)
    return p[:, :3] * scale[:, None]


def _decode_position_float(raw: np.ndarray) -> np.ndarray:
    """(n,3|4) float32 -> (n,3) float32 with the same W-scale convention
    (W > 0 means scale; matches VertexLoadHelper.CreatVector4Float)."""
    p = np.asarray(raw, dtype=np.float32)
    if p.shape[1] == 3:
        return p.copy()
    w = p[:, 3]
    scale = np.where(w > 0.0, w, 1.0)
    return p[:, :3] * scale[:, None]


def encode_position_half4(pos: np.ndarray, high_precision: bool = True,
                          chunk: int = 2048) -> np.ndarray:
    """(n,3) float32 -> (n,4) float16 using CA's W-scale trick.

    high_precision brute-forces the 1024 half-float mantissa values of W in
    [1, 2) per vertex (vectorised port of
    VertexLoadHelper.ConvertertVertexToHalfExtraPrecision).  With
    high_precision=False, W is 1.0 and XYZ are stored directly.
    """
    pos = np.asarray(pos, dtype=np.float32)
    n = len(pos)
    out = np.empty((n, 4), dtype=np.float32)

    max_abs = float(np.abs(pos).max()) if n else 0.0
    if max_abs >= 65504.0:
        raise RmvFormatError(
            f"Vertex position magnitude {max_abs:.1f} exceeds the half-float "
            "range (65504) used by RMV2. Scale the model down.")

    if not high_precision or n == 0:
        out[:, :3] = pos
        out[:, 3] = 1.0
        return out.astype(np.float16)

    # All candidates 1 + k/1024 are exactly representable as float16.
    cand = 1.0 + np.arange(1024, dtype=np.float32) / 1024.0
    best_w = np.ones(n, dtype=np.float32)
    for start in range(0, n, chunk):
        p = pos[start:start + chunk]                       # (c,3)
        scaled = p[:, None, :] / cand[None, :, None]       # (c,1024,3)
        recon = scaled.astype(np.float16).astype(np.float32) \
            * cand[None, :, None]
        err = np.abs(recon - p[:, None, :]).sum(axis=2)    # (c,1024)
        best_w[start:start + chunk] = cand[err.argmin(axis=1)]

    out[:, :3] = pos / best_w[:, None]
    out[:, 3] = best_w
    return out.astype(np.float16)


def _decode_byte_vec(raw: np.ndarray) -> np.ndarray:
    """(n,4) uint8 -> (n,3) float32 in [-1,1].

    Matches VertexLoadHelper.CreatVector3_FromByte including its W-scale
    behaviour (if the decoded W is > 0, XYZ are multiplied by it).
    """
    v = raw.astype(np.float32) / 255.0 * 2.0 - 1.0
    w = v[:, 3]
    xyz = v[:, :3]
    return np.where((w > 0.0)[:, None], xyz * w[:, None], xyz)


def _encode_byte_vec(vec: np.ndarray, w: float = -1.0) -> np.ndarray:
    """(n,3) float32 in [-1,1] -> (n,4) uint8. W defaults to -1 (byte 0),
    matching VertexLoadHelper.CreateNormalVector3."""
    n = len(vec)
    out = np.empty((n, 4), dtype=np.float32)
    out[:, :3] = vec
    out[:, 3] = w
    b = np.round((out + 1.0) * 0.5 * 255.0)
    return np.clip(b, 0, 255).astype(np.uint8)


def _quantize_weights(weights: np.ndarray) -> np.ndarray:
    """(n,k) float weights in [0,1] -> (n,k) uint8 that sums to exactly 255
    per row (the residual after rounding is applied to the largest weight)."""
    w = np.asarray(weights, dtype=np.float32)
    n, k = w.shape
    q = np.round(w * 255.0).astype(np.int32)
    residual = 255 - q.sum(axis=1)
    q[np.arange(n), w.argmax(axis=1)] += residual
    return np.clip(q, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Mesh data container
# ---------------------------------------------------------------------------

@dataclass
class RmvMeshData:
    """Decoded vertex/index data in game space (right-handed, Y-up)."""
    positions: np.ndarray            # (n,3) float32
    normals: np.ndarray              # (n,3) float32
    tangents: np.ndarray             # (n,3) float32
    binormals: np.ndarray            # (n,3) float32
    uv0: np.ndarray                  # (n,2) float32
    uv1: np.ndarray                  # (n,2) float32 (only meaningful: Static)
    colours: np.ndarray              # (n,4) float32 in [0,1] (raw byte/255)
    bone_indices: np.ndarray         # (n,k) uint8, k in {0,2,4}
    bone_weights: np.ndarray         # (n,k) float32
    indices: np.ndarray              # (m,) uint16, DirectX winding (CW front)

    # Original vertex bytes, kept on load so an unmodified mesh re-saves
    # byte-identically (quantization is lossy, passthrough is not).  Set
    # raw_block = None after modifying any vertex array.
    raw_block: Optional[bytes] = None
    raw_format: int = -1
    raw_version: int = -1

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @staticmethod
    def empty(vertex_count: int = 0, weight_count: int = 0) -> "RmvMeshData":
        n = vertex_count
        return RmvMeshData(
            positions=np.zeros((n, 3), np.float32),
            normals=np.zeros((n, 3), np.float32),
            tangents=np.zeros((n, 3), np.float32),
            binormals=np.zeros((n, 3), np.float32),
            uv0=np.zeros((n, 2), np.float32),
            uv1=np.zeros((n, 2), np.float32),
            colours=np.zeros((n, 4), np.float32),
            bone_indices=np.zeros((n, weight_count), np.uint8),
            bone_weights=np.zeros((n, weight_count), np.float32),
            indices=np.zeros((0,), np.uint16),
        )


# ---------------------------------------------------------------------------
# Vertex format <-> numpy structured dtypes
# ---------------------------------------------------------------------------

def _vertex_dtype(vertex_format: int, version: int) -> np.dtype:
    v8 = version == 8
    if vertex_format == VF_STATIC:
        fields = [("pos", "<f2", (4,)), ("uv", "<f2", (2,)),
                  ("uv2", "<f2", (2,)), ("normal", "u1", (4,)),
                  ("tangent", "u1", (4,)), ("binormal", "u1", (4,)),
                  ("col", "u1", (4,))]
    elif vertex_format == VF_WEIGHTED:
        fields = [("pos", "<f2", (4,)), ("bidx", "u1", (2,)),
                  ("bwgt", "u1", (2,)), ("normal", "u1", (4,)),
                  ("uv", "<f2", (2,)), ("binormal", "u1", (4,)),
                  ("tangent", "u1", (4,))]
        if v8:
            fields.append(("col", "u1", (4,)))
    elif vertex_format == VF_CINEMATIC:
        fields = [("pos", "<f2", (4,)), ("bidx", "u1", (4,)),
                  ("bwgt", "u1", (4,)), ("normal", "u1", (4,)),
                  ("uv", "<f2", (2,)), ("binormal", "u1", (4,)),
                  ("tangent", "u1", (4,))]
        if v8:
            fields.append(("col", "u1", (4,)))
    elif vertex_format == VF_COLLISION:
        fields = [("pos", "<f4", (3,)), ("normal", "<f4", (3,))]
    elif vertex_format == VF_POSITION16:
        fields = [("pos", "<f4", (4,))]
    elif vertex_format == VF_CUSTOM_TERRAIN:
        fields = [("pos", "<f4", (4,)), ("normal", "<f4", (4,)),
                  ("uv", "<f2", (2,))]
    elif vertex_format == VF_CUSTOM_TERRAIN2:
        fields = [("pos", "<f4", (4,)), ("normal", "<f4", (4,)),
                  ("uv", "<f2", (2,)), ("col0", "u1", (4,)),
                  ("col1", "u1", (4,)), ("col2", "u1", (4,))]
    elif vertex_format == VF_S2_POSITION_UV:
        fields = [("pos", "<f2", (4,)), ("uv", "<f2", (2,))]
    elif vertex_format == VF_S2_STATIC_NO_UV2:
        fields = [("pos", "<f2", (4,)), ("uv", "<f2", (2,)),
                  ("normal", "u1", (4,)), ("tangent", "u1", (4,)),
                  ("binormal", "u1", (4,)), ("col", "u1", (4,))]
    elif vertex_format == VF_S2_STATIC_FLOAT:
        fields = [("pos", "<f4", (3,)), ("uv", "<f4", (2,)),
                  ("uv2", "<f4", (2,)), ("normal", "u1", (4,)),
                  ("tangent", "u1", (4,)), ("binormal", "u1", (4,)),
                  ("col", "u1", (4,))]
    elif vertex_format == VF_TREE_BILLBOARD:
        fields = [("pos", "<f2", (4,)), ("normal", "<f2", (4,)),
                  ("uv", "<f2", (2,)), ("unknown", "<f2", (4,))]
    elif vertex_format == VF_GRASS:
        fields = [("pos", "<f2", (4,)), ("uv", "<f4", (2,)),
                  ("normal", "u1", (4,)), ("tangent", "u1", (4,)),
                  ("binormal", "u1", (4,))]
    elif vertex_format == VF_VEGETATION:
        # "pivot" is where the vertex sits when the branch is at rest -
        # zero on leaf cards, a small offset on trunks - and "wind" is
        # eight halves that are constant per mesh except for two: they
        # read as per-vertex sway weights.  Neither has anywhere to live
        # in RmvMeshData, so this layout is read-only.
        fields = [("pivot", "<f2", (4,)), ("pos", "<f2", (4,)),
                  ("normal", "<f2", (4,)), ("tangent", "<f2", (4,)),
                  ("binormal", "<f2", (4,)), ("uv", "<f2", (2,)),
                  ("wind", "<f2", (8,))]
    elif vertex_format == VF_S2_BOW_WAVE:
        # The second position is where the wave crest travels to; it has
        # no equivalent in RmvMeshData and is preserved via raw_block.
        fields = [("pos", "<f2", (4,)), ("pos2", "<f2", (4,)),
                  ("uv", "<f2", (2,)), ("unknown", "<f4")]
    else:
        raise RmvFormatError(
            f"Unsupported vertex format {vertex_format} "
            f"({VERTEX_FORMAT_NAMES.get(vertex_format, 'unknown')})")
    return np.dtype(fields)


def vertex_stride(vertex_format: int, version: int) -> int:
    return _vertex_dtype(vertex_format, version).itemsize


def _weight_count(vertex_format: int) -> int:
    if vertex_format == VF_WEIGHTED:
        return 2
    if vertex_format == VF_CINEMATIC:
        return 4
    return 0


def decode_vertices(buf: bytes, offset: int, count: int, stride: int,
                    vertex_format: int, version: int) -> RmvMeshData:
    """Decode a raw vertex block into an RmvMeshData (indices left empty)."""
    dt = _vertex_dtype(vertex_format, version)
    if dt.itemsize != stride:
        raise RmvFormatError(
            f"Vertex stride mismatch for format "
            f"{VERTEX_FORMAT_NAMES.get(vertex_format, vertex_format)} "
            f"v{version}: file says {stride}, expected {dt.itemsize}")

    raw = np.frombuffer(buf, dtype=dt, count=count, offset=offset)
    names = dt.names
    k = _weight_count(vertex_format)
    mesh = RmvMeshData.empty(count, k)

    if vertex_format in (VF_STATIC, VF_WEIGHTED, VF_CINEMATIC):
        mesh.positions = _decode_position_half4(raw["pos"])
        normals = _decode_byte_vec(raw["normal"])
        tangents = _decode_byte_vec(raw["tangent"])
        binormals = _decode_byte_vec(raw["binormal"])
        if vertex_format == VF_STATIC:
            # The 'default' static format stores X/Z swapped for its frame
            normals = normals[:, ::-1].copy()
            tangents = tangents[:, ::-1].copy()
            binormals = binormals[:, ::-1].copy()
            mesh.uv1 = raw["uv2"].astype(np.float32)
        mesh.normals, mesh.tangents, mesh.binormals = \
            normals, tangents, binormals
        mesh.uv0 = raw["uv"].astype(np.float32)
        if "col" in names:
            mesh.colours = raw["col"].astype(np.float32) / 255.0
        else:
            mesh.colours = np.tile(
                np.array([0.0, 0.0, 0.0, 1.0], np.float32), (count, 1))
        if k:
            mesh.bone_indices = raw["bidx"].copy()
            mesh.bone_weights = raw["bwgt"].astype(np.float32) / 255.0
    elif vertex_format in (VF_S2_POSITION_UV, VF_S2_STATIC_NO_UV2,
                           VF_S2_STATIC_FLOAT, VF_S2_BOW_WAVE):
        # Shogun 2 layouts.  Like the modern Static vertex these store the
        # tangent frame X/Z swapped - verified by comparing the stored
        # normals against normals computed from the triangle geometry.
        if vertex_format == VF_S2_STATIC_FLOAT:
            mesh.positions = np.asarray(raw["pos"], np.float32).copy()
        else:
            mesh.positions = _decode_position_half4(raw["pos"])
        mesh.uv0 = raw["uv"].astype(np.float32)
        if "uv2" in names:
            mesh.uv1 = raw["uv2"].astype(np.float32)
        if "normal" in names:
            mesh.normals = _decode_byte_vec(raw["normal"])[:, ::-1].copy()
            mesh.tangents = _decode_byte_vec(raw["tangent"])[:, ::-1].copy()
            mesh.binormals = \
                _decode_byte_vec(raw["binormal"])[:, ::-1].copy()
        if "col" in names:
            mesh.colours = raw["col"].astype(np.float32) / 255.0
        else:
            mesh.colours = np.tile(
                np.array([0.0, 0.0, 0.0, 1.0], np.float32), (count, 1))
    elif vertex_format in (VF_COLLISION, VF_POSITION16):
        mesh.positions = _decode_position_float(raw["pos"])
    elif vertex_format == VF_TREE_BILLBOARD:
        mesh.positions = _decode_position_half4(raw["pos"])
        mesh.normals = raw["normal"][:, :3].astype(np.float32)
        mesh.uv0 = raw["uv"].astype(np.float32)
    elif vertex_format == VF_GRASS:
        mesh.positions = _decode_position_half4(raw["pos"])
        mesh.uv0 = raw["uv"].astype(np.float32)
        mesh.normals = _decode_byte_vec(raw["normal"])
        mesh.tangents = _decode_byte_vec(raw["tangent"])
        mesh.binormals = _decode_byte_vec(raw["binormal"])
    elif vertex_format == VF_VEGETATION:
        mesh.positions = _decode_position_half4(raw["pos"])
        mesh.normals = raw["normal"][:, :3].astype(np.float32)
        mesh.tangents = raw["tangent"][:, :3].astype(np.float32)
        mesh.binormals = raw["binormal"][:, :3].astype(np.float32)
        mesh.uv0 = raw["uv"].astype(np.float32)
    elif vertex_format in (VF_CUSTOM_TERRAIN, VF_CUSTOM_TERRAIN2):
        mesh.positions = _decode_position_float(raw["pos"])
        mesh.normals = _decode_position_float(raw["normal"])
        mesh.uv0 = raw["uv"].astype(np.float32)
        if vertex_format == VF_CUSTOM_TERRAIN2:
            mesh.colours = raw["col0"].astype(np.float32) / 255.0

    mesh.raw_block = bytes(buf[offset:offset + count * stride])
    mesh.raw_format = vertex_format
    mesh.raw_version = version
    return mesh


def _encode_vertices_shogun2(mesh: RmvMeshData, raw: np.ndarray,
                             dt: np.dtype, vertex_format: int,
                             high_precision: bool) -> bytes:
    """Encode the Shogun 2 vertex layouts.  The tangent frame is stored
    X/Z swapped, exactly as in the modern Static vertex (see
    decode_vertices).  VF_S2_BOW_WAVE is not handled here: it carries a
    second position channel RmvMeshData has nowhere to put, so it stays
    read-only and relies on raw_block passthrough."""
    names = dt.names
    if vertex_format == VF_S2_STATIC_FLOAT:
        raw["pos"] = np.asarray(mesh.positions, np.float32)
        raw["uv"] = np.asarray(mesh.uv0, np.float32)
        raw["uv2"] = np.asarray(mesh.uv1, np.float32)
    else:
        raw["pos"] = encode_position_half4(mesh.positions, high_precision)
        raw["uv"] = mesh.uv0.astype(np.float16)

    if "normal" in names:
        raw["normal"] = _encode_byte_vec(mesh.normals[:, ::-1])
        raw["tangent"] = _encode_byte_vec(mesh.tangents[:, ::-1])
        raw["binormal"] = _encode_byte_vec(mesh.binormals[:, ::-1])
    if "col" in names:
        raw["col"] = np.clip(
            np.round(mesh.colours * 255.0), 0, 255).astype(np.uint8)
    return raw.tobytes()


def encode_vertices(mesh: RmvMeshData, vertex_format: int, version: int,
                    high_precision: bool = True) -> bytes:
    """Encode mesh vertex data for the given format/version.

    If the mesh still carries its original raw bytes (loaded and unmodified)
    and the layout matches, they are passed through unchanged so re-saving a
    file is lossless."""
    dt = _vertex_dtype(vertex_format, version)

    if (mesh.raw_block is not None
            and mesh.raw_format == vertex_format
            and (mesh.raw_version == 8) == (version == 8)
            and len(mesh.raw_block) == dt.itemsize * mesh.vertex_count):
        return mesh.raw_block

    n = mesh.vertex_count
    raw = np.zeros(n, dtype=dt)
    names = dt.names

    if vertex_format in (VF_S2_POSITION_UV, VF_S2_STATIC_NO_UV2,
                         VF_S2_STATIC_FLOAT):
        return _encode_vertices_shogun2(mesh, raw, dt, vertex_format,
                                        high_precision)

    if vertex_format not in (VF_STATIC, VF_WEIGHTED, VF_CINEMATIC):
        raise RmvFormatError(
            f"Writing vertex format "
            f"{VERTEX_FORMAT_NAMES.get(vertex_format, vertex_format)} "
            "is not supported (read-only format)")

    raw["pos"] = encode_position_half4(mesh.positions, high_precision)

    normals, tangents, binormals = \
        mesh.normals, mesh.tangents, mesh.binormals
    if vertex_format == VF_STATIC:
        normals = normals[:, ::-1]
        tangents = tangents[:, ::-1]
        binormals = binormals[:, ::-1]
        raw["uv2"] = mesh.uv1.astype(np.float16)
    raw["normal"] = _encode_byte_vec(normals)
    raw["tangent"] = _encode_byte_vec(tangents)
    raw["binormal"] = _encode_byte_vec(binormals)
    raw["uv"] = mesh.uv0.astype(np.float16)

    if "col" in names:
        raw["col"] = np.clip(
            np.round(mesh.colours * 255.0), 0, 255).astype(np.uint8)

    k = _weight_count(vertex_format)
    if k:
        if mesh.bone_indices.shape[1] != k or mesh.bone_weights.shape[1] != k:
            raise RmvFormatError(
                f"Vertex format {VERTEX_FORMAT_NAMES[vertex_format]} needs "
                f"{k} bone influences per vertex, got "
                f"{mesh.bone_indices.shape[1]}")
        raw["bidx"] = mesh.bone_indices
        raw["bwgt"] = _quantize_weights(mesh.bone_weights)

    return raw.tobytes()


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

@dataclass
class RmvAttachmentPoint:
    name: str = ""
    matrix: tuple = tuple(
        (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0))
    bone_index: int = 0


IDENTITY_3X4 = (1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0)


@dataclass
class WeightedMaterial:
    """The material header used by the weighted family, default_type, decal,
    dirtmap etc. (everything except the two terrain materials)."""
    material_id: int = MAT_WEIGHTED
    vertex_format: int = VF_CINEMATIC
    # What the file declared, when that is not the layout the vertex
    # block actually has - see _FORMAT_BY_DECLARED_STRIDE.  None means
    # the two agree and vertex_format is written as it stands.
    declared_vertex_format: Optional[int] = None
    model_name: str = ""
    texture_directory: str = ""
    filters: str = ""
    padding2: tuple = (0, 0)
    pivot: tuple = (0.0, 0.0, 0.0)
    matrices: tuple = (IDENTITY_3X4, IDENTITY_3X4, IDENTITY_3X4)
    matrix_index: int = -1
    parent_matrix_index: int = -1
    padding124: bytes = b"\0" * 124
    attachment_points: list = field(default_factory=list)
    textures: list = field(default_factory=list)      # [(type:int, path:str)]
    string_params: list = field(default_factory=list)  # [(index, str)]
    float_params: list = field(default_factory=list)   # [(index, float)]
    int_params: list = field(default_factory=list)     # [(index, int)]
    vec4_params: list = field(default_factory=list)    # [(index, (x,y,z,w))]

    def compute_size(self, version: int = 6) -> int:
        string_size = sum(4 + 2 + len(v.encode("utf-8", errors="replace"))
                          for _, v in self.string_params)
        return (_weighted_material_struct(version).size
                + _attachment_point_struct(version).size
                * len(self.attachment_points)
                + _texture_struct(version).size * len(self.textures)
                + string_size
                + len(self.int_params) * 8
                + len(self.float_params) * 8
                + len(self.vec4_params) * 20)

    def get_texture(self, texture_type: int) -> Optional[str]:
        for ttype, path in self.textures:
            if ttype == texture_type:
                return path
        return None

    def get_int_param(self, index: int) -> Optional[int]:
        for i, v in self.int_params:
            if i == index:
                return v
        return None

    @staticmethod
    def parse(buf: bytes, offset: int, version: int = 6) -> "WeightedMaterial":
        wide = _wide_strings(version)
        mat_struct = _weighted_material_struct(version)
        vals = mat_struct.unpack_from(buf, offset)
        (vertex_type, model_name, texture_dir, filters,
         pad0, pad1) = vals[0:6]
        pivot = vals[6:9]
        matrices = (tuple(vals[9:21]), tuple(vals[21:33]), tuple(vals[33:45]))
        matrix_index, parent_matrix_index = vals[45:47]
        (attach_count, tex_count, str_count,
         float_count, int_count, vec4_count) = vals[47:53]
        padding124 = vals[53]

        mat = WeightedMaterial(
            vertex_format=vertex_type,
            model_name=_decode_name(model_name, wide),
            texture_directory=_decode_name(texture_dir, wide),
            filters=_decode_name(filters, wide),
            padding2=(pad0, pad1),
            pivot=tuple(pivot),
            matrices=matrices,
            matrix_index=matrix_index,
            parent_matrix_index=parent_matrix_index,
            padding124=padding124,
        )

        attach_struct = _attachment_point_struct(version)
        tex_struct = _texture_struct(version)
        pos = offset + mat_struct.size
        for _ in range(attach_count):
            a = attach_struct.unpack_from(buf, pos)
            mat.attachment_points.append(RmvAttachmentPoint(
                name=_decode_name(a[0], wide),
                matrix=tuple(a[1:13]),
                bone_index=a[13]))
            pos += attach_struct.size
        for _ in range(tex_count):
            ttype, tpath = tex_struct.unpack_from(buf, pos)
            mat.textures.append((ttype, _decode_name(tpath, wide)))
            pos += tex_struct.size
        for _ in range(str_count):
            (idx,) = struct.unpack_from("<i", buf, pos)
            value, read = _read_ca_string(buf, pos + 4)
            mat.string_params.append((idx, value))
            pos += 4 + read
        for _ in range(float_count):
            idx, value = struct.unpack_from("<if", buf, pos)
            mat.float_params.append((idx, value))
            pos += 8
        for _ in range(int_count):
            idx, value = struct.unpack_from("<ii", buf, pos)
            mat.int_params.append((idx, value))
            pos += 8
        for _ in range(vec4_count):
            vals = struct.unpack_from("<i4f", buf, pos)
            mat.vec4_params.append((vals[0], tuple(vals[1:])))
            pos += 20
        return mat

    def write(self, version: int = 6) -> bytes:
        wide = _wide_strings(version)
        pad = self.padding124
        if len(pad) != 124:
            pad = (bytes(pad) + b"\0" * 124)[:124]
        declared = (self.vertex_format if self.declared_vertex_format is None
                    else self.declared_vertex_format)
        header = _weighted_material_struct(version).pack(
            declared & 0xFFFF,
            _encode_name(self.model_name, 32, wide),
            _encode_name(self.texture_directory, 256, wide),
            _encode_name(self.filters, 256, wide),
            int(self.padding2[0]) & 0xFF, int(self.padding2[1]) & 0xFF,
            *[float(v) for v in self.pivot],
            *[float(v) for m in self.matrices for v in m],
            int(self.matrix_index), int(self.parent_matrix_index),
            len(self.attachment_points), len(self.textures),
            len(self.string_params), len(self.float_params),
            len(self.int_params), len(self.vec4_params),
            pad)

        parts = [header]
        for ap in self.attachment_points:
            parts.append(_attachment_point_struct(version).pack(
                _encode_name(ap.name, 32, wide),
                *[float(v) for v in ap.matrix],
                int(ap.bone_index)))
        for ttype, tpath in self.textures:
            parts.append(_texture_struct(version).pack(
                int(ttype), _encode_name(tpath, 256, wide)))
        for idx, value in self.string_params:
            parts.append(struct.pack("<i", idx) + _write_ca_string(value))
        for idx, value in self.float_params:
            parts.append(struct.pack("<if", idx, float(value)))
        for idx, value in self.int_params:
            parts.append(struct.pack("<ii", idx, int(value)))
        for idx, value in self.vec4_params:
            parts.append(struct.pack("<i4f", idx, *[float(v) for v in value]))
        blob = b"".join(parts)
        if len(blob) != self.compute_size(version):
            raise RmvFormatError("WeightedMaterial size mismatch on write")
        return blob


@dataclass
class TerrainTileMaterial:
    """Material id 101 (TerrainTiles). Read/write of the raw struct only."""
    material_id: int = MAT_TERRAIN_TILES
    vertex_format: int = VF_POSITION16
    model_name: str = "TerrainTile"
    name_raw: bytes = b"\0" * 64
    unknowns: tuple = (0, 0, 0, 0, 0, 0)
    # Interface parity with WeightedMaterial
    pivot: tuple = (0.0, 0.0, 0.0)
    textures: list = field(default_factory=list)
    attachment_points: list = field(default_factory=list)
    string_params: list = field(default_factory=list)
    float_params: list = field(default_factory=list)
    int_params: list = field(default_factory=list)
    vec4_params: list = field(default_factory=list)
    texture_directory: str = ""
    filters: str = ""
    matrix_index: int = -1
    parent_matrix_index: int = -1

    def compute_size(self, version: int = 6) -> int:
        return _TERRAIN_TILE_MATERIAL.size

    def get_texture(self, texture_type: int):
        return None

    def get_int_param(self, index: int):
        return None

    @staticmethod
    def parse(buf: bytes, offset: int) -> "TerrainTileMaterial":
        vals = _TERRAIN_TILE_MATERIAL.unpack_from(buf, offset)
        return TerrainTileMaterial(
            name_raw=vals[0],
            model_name=_decode_fixed_string(vals[0]) or "TerrainTile",
            unknowns=tuple(vals[1:7]))

    def write(self, version: int = 6) -> bytes:
        return _TERRAIN_TILE_MATERIAL.pack(self.name_raw, *self.unknowns)


@dataclass
class CustomTerrainMaterial:
    """Material id 49 (custom_terrain)."""
    material_id: int = MAT_CUSTOM_TERRAIN
    vertex_format: int = VF_CUSTOM_TERRAIN
    model_name: str = "TerrainTile"
    texture_path: str = ""
    path_raw: bytes = b""
    # Interface parity with WeightedMaterial
    pivot: tuple = (0.0, 0.0, 0.0)
    textures: list = field(default_factory=list)
    attachment_points: list = field(default_factory=list)
    string_params: list = field(default_factory=list)
    float_params: list = field(default_factory=list)
    int_params: list = field(default_factory=list)
    vec4_params: list = field(default_factory=list)
    texture_directory: str = ""
    filters: str = ""
    matrix_index: int = -1
    parent_matrix_index: int = -1

    def compute_size(self, version: int = 6) -> int:
        return _CUSTOM_TERRAIN_MATERIAL.size

    def get_texture(self, texture_type: int):
        return None

    def get_int_param(self, index: int):
        return None

    @staticmethod
    def parse(buf: bytes, offset: int) -> "CustomTerrainMaterial":
        (path,) = _CUSTOM_TERRAIN_MATERIAL.unpack_from(buf, offset)
        return CustomTerrainMaterial(texture_path=_decode_fixed_string(path),
                                     path_raw=path)

    def write(self, version: int = 6) -> bytes:
        # Rome 2 leaves whatever was last in memory after the string's
        # terminator - path names, tile ids, plain junk - so an untouched
        # path is written back verbatim rather than re-padded with zeros.
        if (len(self.path_raw) == _CUSTOM_TERRAIN_MATERIAL.size
                and _decode_fixed_string(self.path_raw) == self.texture_path):
            return self.path_raw
        return _CUSTOM_TERRAIN_MATERIAL.pack(
            _encode_fixed_string(self.texture_path, 256))


@dataclass
class Shogun2Material:
    """Material header used by RMV2 v1 to v3 (Shogun 2, and Rome 2's
    user-interface models).

    Shogun 2 predates the tagged texture/parameter lists of the modern
    format: a material is a run of fixed-width, zero-padded UTF-16LE
    fields whose exact composition depends on the material id.

        v2 only     shader name     64 bytes  ("rigid_default")
        (not id 22) model name      64 bytes  ("display_hull_LOD1")
                    texture paths   512 bytes each, 0..n of them
        optional    tail            a trailing i32 bone index, and for
                                    some ids 8 further unidentified bytes

    Only a handful of material ids have been seen, so rather than assume
    a table that vanilla files would immediately violate, the block is
    kept verbatim in `raw` and the field boundaries are derived from its
    length.  write() patches the understood fields back into `raw`, which
    keeps re-saving an unmodified file byte-identical whatever the id.
    """
    material_id: int = 21
    vertex_format: int = VF_STATIC
    version: int = 2
    raw: bytes = b""
    model_name: str = ""
    shader_name: str = ""
    texture_paths: list = field(default_factory=list)   # [str]
    # -1 = not attached to a bone.  None = this material has no bone field.
    bone_index: Optional[int] = None
    # Byte offsets into `raw` for the fields we understand, so write() can
    # patch them back without having to re-derive the layout, plus the
    # values as first parsed.  CA's fixed-width fields often keep junk
    # after the terminator (the tail of a longer name that was overwritten
    # in place), so an unchanged field is left exactly as it was rather
    # than re-encoded and zero-padded.
    _offsets: dict = field(default_factory=dict)
    _original: dict = field(default_factory=dict)

    # Interface parity with WeightedMaterial ------------------------------
    pivot: tuple = (0.0, 0.0, 0.0)
    attachment_points: list = field(default_factory=list)
    string_params: list = field(default_factory=list)
    float_params: list = field(default_factory=list)
    int_params: list = field(default_factory=list)
    vec4_params: list = field(default_factory=list)
    filters: str = ""
    parent_matrix_index: int = -1

    @property
    def matrix_index(self) -> int:
        """The single bone this mesh rides on, or -1 when unattached.

        Shogun 2 has no per-vertex skinning in this format; a mesh is
        rigidly parented to one bone, which is exactly what the modern
        format calls matrix_index.
        """
        return -1 if self.bone_index is None else self.bone_index

    @matrix_index.setter
    def matrix_index(self, value: int):
        if self.bone_index is not None:
            self.bone_index = int(value)

    @property
    def texture_directory(self) -> str:
        return self.texture_paths[0] if self.texture_paths else ""

    @property
    def textures(self) -> list:
        """The texture paths as (type, path) pairs, for parity with the
        modern material.

        Shogun 2 stores no type tag: a mesh names a texture *set* and the
        engine appends _diffuse/_normal/_gloss_map to it.  In the files
        seen, slot 0 is that set and slot 1 (ship materials only) is an
        ambient occlusion map; slots beyond that were always empty and are
        reported untyped.  Empty slots are skipped.
        """
        slots = (TEXTURE_TYPE_DIFFUSE, TEXTURE_TYPE_AMBIENT_OCCLUSION)
        out = []
        for i, path in enumerate(self.texture_paths):
            if path:
                out.append((slots[i] if i < len(slots) else -1, path))
        return out

    def get_texture(self, texture_type: int) -> Optional[str]:
        for ttype, path in self.textures:
            if ttype == texture_type:
                return path
        return None

    def get_int_param(self, index: int):
        return None

    def compute_size(self, version: int = 6) -> int:
        return len(self.raw)

    @staticmethod
    def build(version: int, material_id: int = 21, model_name: str = "",
              shader_name: str = "rigid_default",
              texture_paths=(), bone_index: Optional[int] = -1,
              texture_slots: int = 2) -> "Shogun2Material":
        """Create a material block from scratch (for meshes authored in
        Blender rather than loaded from a file).

        The default shape - shader + model name, two texture slots and a
        bone index - is what vanilla material 21 uses, the one animated
        props are built from.  Pass bone_index=None for the ids that have
        no bone field at all (32 and friends).
        """
        blob = bytearray()
        if version >= 2:
            blob += _encode_fixed_utf16(shader_name, _S2_STR32)
        blob += _encode_fixed_utf16(model_name, _S2_STR32)
        paths = list(texture_paths)[:texture_slots]
        paths += [""] * (texture_slots - len(paths))
        for path in paths:
            blob += _encode_fixed_utf16(path, _S2_STR256)
        if bone_index is not None:
            blob += struct.pack("<i", bone_index)
        return Shogun2Material.parse(bytes(blob), 0, material_id, version,
                                     len(blob))

    @staticmethod
    def parse(buf: bytes, offset: int, material_id: int, version: int,
              size: int) -> "Shogun2Material":
        raw = bytes(buf[offset:offset + size])
        mat = Shogun2Material(material_id=material_id, version=version,
                              raw=raw)
        pos = 0

        def take_str(key: str, width: int) -> str:
            nonlocal pos
            value = _decode_fixed_utf16(raw[pos:pos + width])
            mat._offsets[key] = pos
            mat._original[key] = value
            pos += width
            return value

        # Material 22 (bow_wave) carries a shader name and nothing else;
        # every other id seen starts with the model name in v1 and with
        # shader + model name in v2 and v3.
        if version >= 2 and size >= _S2_STR32:
            mat.shader_name = take_str("shader_name", _S2_STR32)
        if size - pos >= _S2_STR32:
            mat.model_name = take_str("model_name", _S2_STR32)

        for i in range((size - pos) // _S2_STR256):
            mat.texture_paths.append(take_str(f"texture{i}", _S2_STR256))

        # Whatever is left is the tail.  The bone index is its first i32
        # in every sample; the 8 extra bytes material 54 carries after it
        # are always zero and stay untouched inside `raw`.
        if size - pos >= 4:
            mat.bone_index = struct.unpack_from("<i", raw, pos)[0]
            mat._offsets["bone_index"] = pos
            mat._original["bone_index"] = mat.bone_index
        return mat

    def write(self, version: int = 6) -> bytes:
        out = bytearray(self.raw)

        def put_str(key: str, value: str, width: int):
            at = self._offsets.get(key)
            if at is None or self._original.get(key) == value:
                return          # unchanged: keep the bytes CA wrote
            out[at:at + width] = _encode_fixed_utf16(value, width)

        put_str("shader_name", self.shader_name, _S2_STR32)
        put_str("model_name", self.model_name, _S2_STR32)
        for i, path in enumerate(self.texture_paths):
            put_str(f"texture{i}", path, _S2_STR256)
        at = self._offsets.get("bone_index")
        if at is not None and self.bone_index is not None:
            struct.pack_into("<i", out, at, self.bone_index)
        return bytes(out)


def _parse_material(buf: bytes, offset: int, material_id: int,
                    expected_size: int, version: int = 6):
    """Dispatch like MaterialFactory: terrain ids get their own headers,
    everything else uses the weighted material layout."""
    if material_id == MAT_TERRAIN_TILES:
        mat = TerrainTileMaterial.parse(buf, offset)
    elif material_id == MAT_CUSTOM_TERRAIN:
        mat = CustomTerrainMaterial.parse(buf, offset)
    else:
        mat = WeightedMaterial.parse(buf, offset, version)
        mat.material_id = material_id
    actual = mat.compute_size(version)
    if actual != expected_size:
        raise RmvFormatError(
            f"Material {MATERIAL_NAMES.get(material_id, material_id)} header "
            f"size mismatch: read {actual}, expected {expected_size} bytes")
    return mat


# ---------------------------------------------------------------------------
# Model / LOD / File containers
# ---------------------------------------------------------------------------

DEFAULT_SHADER_NAME = "default_dry"


@dataclass
class RmvModel:
    material: object = field(default_factory=WeightedMaterial)
    mesh: RmvMeshData = field(default_factory=RmvMeshData.empty)
    render_flag: int = 0
    shader_name: str = DEFAULT_SHADER_NAME
    # The shader name as it sits in the file.  CA's field often keeps
    # a byte or two of whatever it last held after the terminator -
    # Rome 2's tree billboards all carry one - so an unedited name
    # goes back exactly as it came, rather than zero-padded.
    shader_raw: bytes = b""
    shader_extra: bytes = b"\0" * 10   # "UnknownValues" in the C# reference
    shader_zero: bytes = b"\0" * 10
    # None means "compute from the mesh on save"; loading fills these in so
    # unmodified files keep their original values.
    bbox_min: Optional[tuple] = None
    bbox_max: Optional[tuple] = None
    # Shogun 2 non-renderable meshes declare a vertex count but ship no
    # vertex block; None means "use the mesh's real count" (the normal
    # case).  See _load_shogun2_model.
    declared_vertex_count: Optional[int] = None

    @property
    def written_vertex_count(self) -> int:
        if self.declared_vertex_count is not None and \
                not self.mesh.vertex_count:
            return self.declared_vertex_count
        return self.mesh.vertex_count

    def computed_bbox(self) -> tuple:
        if self.bbox_min is not None and self.bbox_max is not None:
            return tuple(self.bbox_min), tuple(self.bbox_max)
        if self.mesh.vertex_count:
            return (tuple(self.mesh.positions.min(axis=0).tolist()),
                    tuple(self.mesh.positions.max(axis=0).tolist()))
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)


# Stamped into the 3 bytes after quality_level on every LOD header this
# add-on writes (see save()), so a file can be recognized as having passed
# through this exporter. Those bytes are compiler struct-alignment padding
# with no meaning to the game or to CA's tools - gen_tree_oak_large_03
# carries the same stale (157, 55, 149) on all 3 of its LODs while the real
# quality byte in front of them varies 2/0/0, and AssetEditor stamps its own
# arbitrary (125, 136, 174) - so repurposing them is safe for the game.
# RPFM reads quality_level as a u32 spanning all 4 bytes
# (rpfm_lib/src/files/rigidmodel/versions/v8.rs) and casts that to i32, so
# the last byte is kept < 0x80: that keeps the sign bit clear so RPFM never
# clamps the displayed value to a silently-wrong 0, at the cost of RPFM
# showing a large (but honest, non-zero, obviously-not-a-quality-level)
# number instead of the real quality byte while this signature is present.
EXPORT_SIGNATURE = (0x52, 0x62, 0x00)  # ASCII 'R', 'b', + sign-safe 0 guard


@dataclass
class RmvLod:
    models: list = field(default_factory=list)
    camera_distance: float = 0.0
    lod_level: int = 0
    quality_level: int = 0
    # What was actually read from the 3 padding bytes on load (or (0, 0, 0)
    # for a freshly-created LOD). save() ignores this and always writes
    # EXPORT_SIGNATURE instead - this field exists so loaded values are
    # available to inspect/debug, not because save() round-trips them.
    padding: tuple = (0, 0, 0)
    # Shogun 2 only.  The lod header's total vertex/index byte counts are
    # normally just the sum of the meshes' blocks and are recomputed on
    # save, but some CA materials ship them deliberately zeroed (bow_wave)
    # even though real geometry follows.  When a loaded file disagrees
    # with its own geometry the declared pair is kept here and written
    # back verbatim, so such files re-save byte-identically.
    declared_sizes: Optional[tuple] = None


@dataclass
class RmvFile:
    version: int = 7
    skeleton_name: str = ""
    lods: list = field(default_factory=list)
    # Shogun 2 only: the u32 between the lod table and the skeleton name.
    # Zero in every file seen; kept so re-saving stays byte-identical.
    unknown_s2: int = 0

    @property
    def is_shogun2(self) -> bool:
        return self.version in SHOGUN2_VERSIONS


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _lod_header_struct(version: int):
    return _LOD_HEADER_V5_V6 if version <= 6 else _LOD_HEADER_V7_V8


# A mesh whose block does not match the stride of the format it declares.
# The pair (declared format, stride) says which layout it really is, where
# a blind search by stride could not: three different 28-byte layouts are
# in use, and Rome 2 declares two of them under ids of its own.
_FORMAT_BY_DECLARED_STRIDE = {
    (VF_POSITION16, 28): VF_GRASS,
    (VF_CUSTOM_TERRAIN, 60): VF_VEGETATION,
    (VF_ROME2_TREE, 12): VF_POSITION_UV,
    (VF_ROME2_TREE, 28): VF_TREE_BILLBOARD,
    (VF_ROME2_WATER, 12): VF_POSITION_UV,
}


def _load_model(buf: bytes, offset: int, version: int) -> tuple[RmvModel, int]:
    common = _common_header_struct(version)
    vals = common.unpack_from(buf, offset)
    (model_type, render_flag, mesh_section_size, vertex_offset,
     vertex_count, index_offset, index_count) = vals[0:7]
    bbox = vals[7:13]
    shader_name, shader_extra, shader_zero = vals[13:16]

    material_offset = offset + common.size
    expected_material_size = (offset + vertex_offset) - material_offset
    material = _parse_material(buf, material_offset, model_type,
                               expected_material_size, version)

    if vertex_count > 0:
        stride = (index_offset - vertex_offset) // vertex_count
        fmt = material.vertex_format
        try:
            expected_stride = vertex_stride(fmt, version)
        except RmvFormatError:
            expected_stride = -1
        if expected_stride != stride:
            # Some files deviate from the declared format; take the known
            # pairing where there is one, else disambiguate using the
            # actual stride (e.g. the v8 colour variants, or CustomTerrain
            # vs CustomTerrain2).
            resolved = _FORMAT_BY_DECLARED_STRIDE.get((fmt, stride))
            for candidate in (VF_STATIC, VF_WEIGHTED, VF_CINEMATIC,
                              VF_COLLISION, VF_POSITION16,
                              VF_CUSTOM_TERRAIN, VF_CUSTOM_TERRAIN2):
                if resolved is not None:
                    break
                if vertex_stride(candidate, version) == stride and \
                        _weight_count(candidate) == _weight_count(fmt):
                    resolved = candidate
                    break
            if resolved is None:
                raise RmvFormatError(
                    f"Unknown vertex layout: format "
                    f"{VERTEX_FORMAT_NAMES.get(fmt, fmt)} with stride "
                    f"{stride} (v{version})")
            # Keep what the file declared: it is what the game reads,
            # and writing our own id back would corrupt the material.
            material.declared_vertex_format = fmt
            fmt = resolved
        mesh = decode_vertices(buf, offset + vertex_offset, vertex_count,
                               stride, fmt, version)
        material.vertex_format = fmt
    else:
        mesh = RmvMeshData.empty(0, _weight_count(material.vertex_format))

    usable = (index_count // 3) * 3
    mesh.indices = np.frombuffer(
        buf, dtype="<u2", count=usable, offset=offset + index_offset).copy()

    model = RmvModel(
        material=material,
        mesh=mesh,
        render_flag=render_flag,
        shader_name=_decode_name(shader_name, _wide_strings(version)),
        shader_raw=shader_name,
        shader_extra=shader_extra,
        shader_zero=shader_zero,
        bbox_min=tuple(bbox[0:3]),
        bbox_max=tuple(bbox[3:6]),
    )
    return model, mesh_section_size


# Shogun 2 materials carry no vertex-format field, so the layout has to be
# identified by stride.  Every stride occurring in the vanilla packs is
# listed here; stride 32 happens to coincide with the modern static vertex.
_S2_FORMAT_BY_STRIDE = {
    12: VF_S2_POSITION_UV,
    24: VF_S2_BOW_WAVE,
    28: VF_S2_STATIC_NO_UV2,
    32: VF_STATIC,
    44: VF_S2_STATIC_FLOAT,
    60: VF_VEGETATION,      # Rome 2's v3 shrubs and hedges
}


def _shogun2_vertex_format(stride: int, material_id: int) -> int:
    try:
        return _S2_FORMAT_BY_STRIDE[stride]
    except KeyError:
        raise RmvFormatError(
            f"Unknown Shogun 2 vertex stride {stride} "
            f"(material {material_id})") from None


def _load_shogun2_model(buf: bytes, offset: int,
                        version: int) -> tuple[RmvModel, int]:
    vals = _S2_COMMON_HEADER.unpack_from(buf, offset)
    (material_id, render_flag, mesh_section_size, vertex_offset,
     vertex_count, index_offset, index_count) = vals[0:7]
    bbox = vals[7:13]

    material_offset = offset + _S2_COMMON_HEADER.size
    material = Shogun2Material.parse(
        buf, material_offset, material_id, version,
        (offset + vertex_offset) - material_offset)

    stride = ((index_offset - vertex_offset) // vertex_count
              if vertex_count else 0)
    if vertex_count > 0 and stride > 0:
        material.vertex_format = _shogun2_vertex_format(stride, material_id)
        mesh = decode_vertices(buf, offset + vertex_offset, vertex_count,
                               stride, material.vertex_format, version)
    else:
        # Non-renderable meshes (materials 26 and 45) declare a vertex
        # count but store no vertex block at all - the index data starts
        # where the vertices would.  Keep the declared count so the file
        # re-saves unchanged.
        mesh = RmvMeshData.empty(0, 0)

    usable = (index_count // 3) * 3
    mesh.indices = np.frombuffer(
        buf, dtype="<u2", count=usable, offset=offset + index_offset).copy()

    model = RmvModel(
        material=material,
        mesh=mesh,
        render_flag=render_flag,
        shader_name=material.shader_name,
        bbox_min=tuple(bbox[0:3]),
        bbox_max=tuple(bbox[3:6]),
        declared_vertex_count=(vertex_count if not mesh.vertex_count
                               else None),
    )
    return model, mesh_section_size


def _load_shogun2(data: bytes, version: int) -> RmvFile:
    """Parse a Shogun 2 (v1/v2) file.  See _S2_FILE_HEADER for the layout
    differences against v5+."""
    magic, version, lod_count = _S2_FILE_HEADER.unpack_from(data, 0)
    if lod_count > 100:
        raise RmvFormatError(f"Implausible lod count {lod_count}")

    pos = _S2_FILE_HEADER.size
    lod_headers = []
    for _ in range(lod_count):
        mesh_count, vsize, isize, first, camera = \
            _LOD_HEADER_V5_V6.unpack_from(data, pos)
        pos += _LOD_HEADER_V5_V6.size
        lod_headers.append((mesh_count, first, camera, (vsize, isize)))

    (unknown,) = struct.unpack_from("<I", data, pos)
    pos += 4
    skeleton = _decode_fixed_utf16(data[pos:pos + _S2_NAME_SIZE])

    rmv = RmvFile(version=version, skeleton_name=skeleton,
                  unknown_s2=unknown)
    for i, (mesh_count, first, camera, declared) in enumerate(lod_headers):
        lod = RmvLod(camera_distance=camera, lod_level=i)
        offset = first
        actual_vertex = actual_index = 0
        for _ in range(mesh_count):
            model, section_size = _load_shogun2_model(data, offset, version)
            lod.models.append(model)
            offset += section_size
            actual_vertex += (model.mesh.vertex_count
                              * vertex_stride(model.material.vertex_format,
                                              version))
            actual_index += len(model.mesh.indices) * 2
        if declared != (actual_vertex, actual_index):
            lod.declared_sizes = declared
        rmv.lods.append(lod)
    return rmv


def load(data: bytes) -> RmvFile:
    """Parse a .rigid_model_v2 file from bytes."""
    if len(data) < _FILE_HEADER.size:
        raise RmvFormatError("File too small to be a RMV2 file")
    magic, version = struct.unpack_from("<4sI", data, 0)
    if magic != b"RMV2":
        raise RmvFormatError(
            f"Not a RigidModel v2 file (magic {magic!r}, expected b'RMV2')")
    if version not in SUPPORTED_VERSIONS:
        raise RmvFormatError(
            f"Unsupported RMV2 version {version} "
            f"(supported: {SUPPORTED_VERSIONS})")
    if version in SHOGUN2_VERSIONS:
        return _load_shogun2(data, version)

    file_header = _file_header_struct(version)
    magic, version, lod_count, skeleton = file_header.unpack_from(data, 0)
    rmv = RmvFile(version=version,
                  skeleton_name=_decode_name(skeleton, _wide_strings(version)))

    lod_struct = _lod_header_struct(version)
    lod_headers = []
    pos = file_header.size
    for _ in range(lod_count):
        vals = lod_struct.unpack_from(data, pos)
        header = {
            "mesh_count": vals[0],
            "total_vertex_size": vals[1],
            "total_index_size": vals[2],
            "first_mesh_offset": vals[3],
            "camera_distance": vals[4],
        }
        if version >= 7:
            header["lod_level"] = vals[5]
            header["quality_level"] = vals[6]
            header["padding"] = tuple(vals[7:10])
        pos += lod_struct.size
        lod_headers.append(header)

    for i, header in enumerate(lod_headers):
        lod = RmvLod(
            camera_distance=header["camera_distance"],
            lod_level=header.get("lod_level", i),
            quality_level=header.get("quality_level", 0),
            # v5/v6 headers have no such field; upgrading to v7+ writes zeros
            padding=header.get("padding", (0, 0, 0)),
        )
        offset = header["first_mesh_offset"]
        for _ in range(header["mesh_count"]):
            model, section_size = _load_model(data, offset, version)
            lod.models.append(model)
            offset += section_size
        rmv.lods.append(lod)

    return rmv


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def _save_shogun2(rmv: RmvFile, high_precision: bool,
                  verify: bool) -> bytes:
    """Serialize a Shogun 2 (v1/v2) file."""
    version = rmv.version
    header_block = (_S2_FILE_HEADER.size
                    + _LOD_HEADER_V5_V6.size * len(rmv.lods)
                    + 4 + _S2_NAME_SIZE)

    encoded = []
    for lod in rmv.lods:
        lod_entries = []
        for model in lod.models:
            mat = model.material
            if not isinstance(mat, Shogun2Material):
                raise RmvFormatError(
                    f"Shogun 2 files need Shogun2Material headers, got "
                    f"{type(mat).__name__} - a model imported from a "
                    f"Rome 2+ file cannot be exported as v{version} "
                    f"without conversion")
            vertex_blob = encode_vertices(model.mesh, mat.vertex_format,
                                          version, high_precision)
            index_blob = model.mesh.indices.astype("<u2").tobytes()
            lod_entries.append(
                (model, mat.write(), vertex_blob, index_blob))
        encoded.append(lod_entries)

    out = bytearray()
    out += _S2_FILE_HEADER.pack(b"RMV2", version, len(rmv.lods))

    running = header_block
    for lod, entries in zip(rmv.lods, encoded):
        total_vertex = sum(len(e[2]) for e in entries)
        total_index = sum(len(e[3]) for e in entries)
        if lod.declared_sizes is not None:
            total_vertex, total_index = lod.declared_sizes
        out += _LOD_HEADER_V5_V6.pack(len(entries), total_vertex,
                                      total_index, running,
                                      lod.camera_distance)
        for model, material_blob, vertex_blob, index_blob in entries:
            running += (_S2_COMMON_HEADER.size + len(material_blob)
                        + len(vertex_blob) + len(index_blob))

    out += struct.pack("<I", rmv.unknown_s2)
    out += _encode_fixed_utf16(rmv.skeleton_name, _S2_NAME_SIZE)

    for lod, entries in zip(rmv.lods, encoded):
        for model, material_blob, vertex_blob, index_blob in entries:
            vertex_offset = _S2_COMMON_HEADER.size + len(material_blob)
            index_offset = vertex_offset + len(vertex_blob)
            section_size = index_offset + len(index_blob)
            bbox_min, bbox_max = model.computed_bbox()

            out += _S2_COMMON_HEADER.pack(
                model.material.material_id & 0xFFFF,
                model.render_flag & 0xFFFF,
                section_size,
                vertex_offset,
                model.written_vertex_count,
                index_offset,
                len(model.mesh.indices),
                *[float(v) for v in bbox_min],
                *[float(v) for v in bbox_max])
            out += material_blob
            out += vertex_blob
            out += index_blob

    blob = bytes(out)
    if verify:
        reloaded = load(blob)
        if len(reloaded.lods) != len(rmv.lods):
            raise RmvFormatError("Save verification failed (lod count)")
    return blob


def save(rmv: RmvFile, high_precision: bool = True,
         verify: bool = True) -> bytes:
    """Serialize an RmvFile to bytes.

    Offsets, section sizes and bounding boxes are recomputed, mirroring
    RmvFile.RecalculateOffsets in the C# reference.  When verify is True the
    result is re-parsed as a sanity check before being returned.
    """
    version = rmv.version
    if version not in SUPPORTED_VERSIONS:
        raise RmvFormatError(f"Unsupported RMV2 version {version}")
    if version in SHOGUN2_VERSIONS:
        return _save_shogun2(rmv, high_precision, verify)

    wide = _wide_strings(version)
    file_header = _file_header_struct(version)
    common = _common_header_struct(version)
    lod_struct = _lod_header_struct(version)
    header_block = file_header.size + lod_struct.size * len(rmv.lods)

    # Encode all meshes first so sizes are known.
    encoded = []  # per lod: list of (model, material_blob, vertex_blob, ...)
    for lod in rmv.lods:
        lod_entries = []
        for model in lod.models:
            mat = model.material
            fmt = mat.vertex_format
            vertex_blob = encode_vertices(model.mesh, fmt, version,
                                          high_precision)
            index_blob = model.mesh.indices.astype("<u2").tobytes()
            material_blob = mat.write(version)
            lod_entries.append(
                (model, material_blob, vertex_blob, index_blob))
        encoded.append(lod_entries)

    # LOD headers with cumulative offsets.
    out = bytearray()
    out += file_header.pack(b"RMV2", version, len(rmv.lods),
                            _encode_name(rmv.skeleton_name, 128, wide))

    running = header_block
    for lod, entries in zip(rmv.lods, encoded):
        total_vertex = sum(len(e[2]) for e in entries)
        total_index = sum(len(e[3]) for e in entries)
        if version <= 6:
            out += lod_struct.pack(len(entries), total_vertex, total_index,
                                   running, lod.camera_distance)
        else:
            pad = EXPORT_SIGNATURE
            out += lod_struct.pack(len(entries), total_vertex, total_index,
                                   running, lod.camera_distance,
                                   lod.lod_level, lod.quality_level & 0xFF,
                                   pad[0] & 0xFF, pad[1] & 0xFF, pad[2] & 0xFF)
        for model, material_blob, vertex_blob, index_blob in entries:
            running += (common.size + len(material_blob)
                        + len(vertex_blob) + len(index_blob))

    # Mesh sections.
    pad_width = 20 if wide else 10
    for lod, entries in zip(rmv.lods, encoded):
        for model, material_blob, vertex_blob, index_blob in entries:
            vertex_offset = common.size + len(material_blob)
            index_offset = vertex_offset + len(vertex_blob)
            section_size = index_offset + len(index_blob)

            shader = _encode_name(model.shader_name, 12, wide)
            if (len(model.shader_raw) == len(shader)
                    and _decode_name(model.shader_raw, wide)
                    == model.shader_name):
                shader = model.shader_raw
            extra = (bytes(model.shader_extra)
                     + b"\0" * pad_width)[:pad_width]
            zero = (bytes(model.shader_zero) + b"\0" * pad_width)[:pad_width]
            bbox_min, bbox_max = model.computed_bbox()

            out += common.pack(
                model.material.material_id & 0xFFFF,
                model.render_flag & 0xFFFF,
                section_size,
                vertex_offset,
                model.mesh.vertex_count,
                index_offset,
                len(model.mesh.indices),
                *[float(v) for v in bbox_min],
                *[float(v) for v in bbox_max],
                shader, extra, zero)
            out += material_blob
            out += vertex_blob
            out += index_blob

    blob = bytes(out)
    if verify:
        reloaded = load(blob)  # raises on inconsistency
        if len(reloaded.lods) != len(rmv.lods):
            raise RmvFormatError("Save verification failed (lod count)")
    return blob
