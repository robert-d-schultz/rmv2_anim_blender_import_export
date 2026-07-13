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

SUPPORTED_VERSIONS = (5, 6, 7, 8)

# VertexFormat
VF_STATIC = 0
VF_COLLISION = 1
VF_WEIGHTED = 3           # 2 bone influences
VF_CINEMATIC = 4          # 4 bone influences
VF_POSITION16 = 5
VF_CUSTOM_TERRAIN = 6
VF_CUSTOM_TERRAIN2 = 13

VERTEX_FORMAT_NAMES = {
    VF_STATIC: "Static",
    VF_COLLISION: "Collision",
    VF_WEIGHTED: "Weighted",
    VF_CINEMATIC: "Cinematic",
    VF_POSITION16: "Position16_bit",
    VF_CUSTOM_TERRAIN: "CustomTerrain",
    VF_CUSTOM_TERRAIN2: "CustomTerrain2",
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
_TERRAIN_TILE_MATERIAL = struct.Struct("<64s6I")         # 88
_CUSTOM_TERRAIN_MATERIAL = struct.Struct("<256s")        # 256

assert _FILE_HEADER.size == 140
assert _COMMON_HEADER.size == 80
assert _WEIGHTED_MATERIAL.size == 860
assert _ATTACHMENT_POINT.size == 84
assert _TEXTURE.size == 260


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
    elif vertex_format in (VF_COLLISION, VF_POSITION16):
        mesh.positions = _decode_position_float(raw["pos"])
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

    def compute_size(self) -> int:
        string_size = sum(4 + 2 + len(v.encode("utf-8", errors="replace"))
                          for _, v in self.string_params)
        return (_WEIGHTED_MATERIAL.size
                + _ATTACHMENT_POINT.size * len(self.attachment_points)
                + _TEXTURE.size * len(self.textures)
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
    def parse(buf: bytes, offset: int) -> "WeightedMaterial":
        vals = _WEIGHTED_MATERIAL.unpack_from(buf, offset)
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
            model_name=_decode_fixed_string(model_name),
            texture_directory=_decode_fixed_string(texture_dir),
            filters=_decode_fixed_string(filters),
            padding2=(pad0, pad1),
            pivot=tuple(pivot),
            matrices=matrices,
            matrix_index=matrix_index,
            parent_matrix_index=parent_matrix_index,
            padding124=padding124,
        )

        pos = offset + _WEIGHTED_MATERIAL.size
        for _ in range(attach_count):
            a = _ATTACHMENT_POINT.unpack_from(buf, pos)
            mat.attachment_points.append(RmvAttachmentPoint(
                name=_decode_fixed_string(a[0]),
                matrix=tuple(a[1:13]),
                bone_index=a[13]))
            pos += _ATTACHMENT_POINT.size
        for _ in range(tex_count):
            ttype, tpath = _TEXTURE.unpack_from(buf, pos)
            mat.textures.append((ttype, _decode_fixed_string(tpath)))
            pos += _TEXTURE.size
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

    def write(self) -> bytes:
        pad = self.padding124
        if len(pad) != 124:
            pad = (bytes(pad) + b"\0" * 124)[:124]
        header = _WEIGHTED_MATERIAL.pack(
            self.vertex_format & 0xFFFF,
            _encode_fixed_string(self.model_name, 32),
            _encode_fixed_string(self.texture_directory, 256),
            _encode_fixed_string(self.filters, 256),
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
            parts.append(_ATTACHMENT_POINT.pack(
                _encode_fixed_string(ap.name, 32),
                *[float(v) for v in ap.matrix],
                int(ap.bone_index)))
        for ttype, tpath in self.textures:
            parts.append(_TEXTURE.pack(
                int(ttype), _encode_fixed_string(tpath, 256)))
        for idx, value in self.string_params:
            parts.append(struct.pack("<i", idx) + _write_ca_string(value))
        for idx, value in self.float_params:
            parts.append(struct.pack("<if", idx, float(value)))
        for idx, value in self.int_params:
            parts.append(struct.pack("<ii", idx, int(value)))
        for idx, value in self.vec4_params:
            parts.append(struct.pack("<i4f", idx, *[float(v) for v in value]))
        blob = b"".join(parts)
        if len(blob) != self.compute_size():
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

    def compute_size(self) -> int:
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

    def write(self) -> bytes:
        return _TERRAIN_TILE_MATERIAL.pack(self.name_raw, *self.unknowns)


@dataclass
class CustomTerrainMaterial:
    """Material id 49 (custom_terrain)."""
    material_id: int = MAT_CUSTOM_TERRAIN
    vertex_format: int = VF_CUSTOM_TERRAIN
    model_name: str = "TerrainTile"
    texture_path: str = ""
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

    def compute_size(self) -> int:
        return _CUSTOM_TERRAIN_MATERIAL.size

    def get_texture(self, texture_type: int):
        return None

    def get_int_param(self, index: int):
        return None

    @staticmethod
    def parse(buf: bytes, offset: int) -> "CustomTerrainMaterial":
        (path,) = _CUSTOM_TERRAIN_MATERIAL.unpack_from(buf, offset)
        return CustomTerrainMaterial(texture_path=_decode_fixed_string(path))

    def write(self) -> bytes:
        return _CUSTOM_TERRAIN_MATERIAL.pack(
            _encode_fixed_string(self.texture_path, 256))


def _parse_material(buf: bytes, offset: int, material_id: int,
                    expected_size: int):
    """Dispatch like MaterialFactory: terrain ids get their own headers,
    everything else uses the weighted material layout."""
    if material_id == MAT_TERRAIN_TILES:
        mat = TerrainTileMaterial.parse(buf, offset)
    elif material_id == MAT_CUSTOM_TERRAIN:
        mat = CustomTerrainMaterial.parse(buf, offset)
    else:
        mat = WeightedMaterial.parse(buf, offset)
        mat.material_id = material_id
    actual = mat.compute_size()
    if actual != expected_size:
        raise RmvFormatError(
            f"Material {MATERIAL_NAMES.get(material_id, material_id)} header "
            f"size mismatch: read {actual}, expected {expected_size} bytes")
    return mat


# ---------------------------------------------------------------------------
# Model / LOD / File containers
# ---------------------------------------------------------------------------

DEFAULT_SHADER_NAME = "default_dry "


@dataclass
class RmvModel:
    material: object = field(default_factory=WeightedMaterial)
    mesh: RmvMeshData = field(default_factory=RmvMeshData.empty)
    render_flag: int = 0
    shader_name: str = DEFAULT_SHADER_NAME
    shader_extra: bytes = b"\0" * 10   # "UnknownValues" in the C# reference
    shader_zero: bytes = b"\0" * 10
    # None means "compute from the mesh on save"; loading fills these in so
    # unmodified files keep their original values.
    bbox_min: Optional[tuple] = None
    bbox_max: Optional[tuple] = None

    def computed_bbox(self) -> tuple:
        if self.bbox_min is not None and self.bbox_max is not None:
            return tuple(self.bbox_min), tuple(self.bbox_max)
        if self.mesh.vertex_count:
            return (tuple(self.mesh.positions.min(axis=0).tolist()),
                    tuple(self.mesh.positions.max(axis=0).tolist()))
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)


@dataclass
class RmvLod:
    models: list = field(default_factory=list)
    camera_distance: float = 0.0
    lod_level: int = 0
    quality_level: int = 0
    padding: tuple = (125, 136, 174)   # the values AssetEditor writes


@dataclass
class RmvFile:
    version: int = 7
    skeleton_name: str = ""
    lods: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _lod_header_struct(version: int):
    return _LOD_HEADER_V5_V6 if version <= 6 else _LOD_HEADER_V7_V8


def _load_model(buf: bytes, offset: int, version: int) -> tuple[RmvModel, int]:
    vals = _COMMON_HEADER.unpack_from(buf, offset)
    (model_type, render_flag, mesh_section_size, vertex_offset,
     vertex_count, index_offset, index_count) = vals[0:7]
    bbox = vals[7:13]
    shader_name, shader_extra, shader_zero = vals[13:16]

    material_offset = offset + _COMMON_HEADER.size
    expected_material_size = (offset + vertex_offset) - material_offset
    material = _parse_material(buf, material_offset, model_type,
                               expected_material_size)

    if vertex_count > 0:
        stride = (index_offset - vertex_offset) // vertex_count
        fmt = material.vertex_format
        try:
            expected_stride = vertex_stride(fmt, version)
        except RmvFormatError:
            expected_stride = -1
        if expected_stride != stride:
            # Some files deviate from the declared format; try to
            # disambiguate using the actual stride (e.g. the v8 colour
            # variants, or CustomTerrain vs CustomTerrain2).
            resolved = None
            for candidate in (VF_STATIC, VF_WEIGHTED, VF_CINEMATIC,
                              VF_COLLISION, VF_POSITION16,
                              VF_CUSTOM_TERRAIN, VF_CUSTOM_TERRAIN2):
                if vertex_stride(candidate, version) == stride and \
                        _weight_count(candidate) == _weight_count(fmt):
                    resolved = candidate
                    break
            if resolved is None:
                raise RmvFormatError(
                    f"Unknown vertex layout: format "
                    f"{VERTEX_FORMAT_NAMES.get(fmt, fmt)} with stride "
                    f"{stride} (v{version})")
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
        shader_name=shader_name.decode("utf-8", errors="replace"),
        shader_extra=shader_extra,
        shader_zero=shader_zero,
        bbox_min=tuple(bbox[0:3]),
        bbox_max=tuple(bbox[3:6]),
    )
    return model, mesh_section_size


def load(data: bytes) -> RmvFile:
    """Parse a .rigid_model_v2 file from bytes."""
    if len(data) < _FILE_HEADER.size:
        raise RmvFormatError("File too small to be a RMV2 file")
    magic, version, lod_count, skeleton = _FILE_HEADER.unpack_from(data, 0)
    if magic != b"RMV2":
        raise RmvFormatError(
            f"Not a RigidModel v2 file (magic {magic!r}, expected b'RMV2')")
    if version not in SUPPORTED_VERSIONS:
        raise RmvFormatError(
            f"Unsupported RMV2 version {version} "
            f"(supported: {SUPPORTED_VERSIONS})")

    rmv = RmvFile(version=version,
                  skeleton_name=_decode_fixed_string(skeleton))

    lod_struct = _lod_header_struct(version)
    lod_headers = []
    pos = _FILE_HEADER.size
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
            padding=header.get("padding", (125, 136, 174)),
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

    lod_struct = _lod_header_struct(version)
    header_block = _FILE_HEADER.size + lod_struct.size * len(rmv.lods)

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
            material_blob = mat.write()
            lod_entries.append(
                (model, material_blob, vertex_blob, index_blob))
        encoded.append(lod_entries)

    # LOD headers with cumulative offsets.
    out = bytearray()
    out += _FILE_HEADER.pack(b"RMV2", version, len(rmv.lods),
                             _encode_fixed_string(rmv.skeleton_name, 128))

    running = header_block
    for lod, entries in zip(rmv.lods, encoded):
        total_vertex = sum(len(e[2]) for e in entries)
        total_index = sum(len(e[3]) for e in entries)
        if version <= 6:
            out += lod_struct.pack(len(entries), total_vertex, total_index,
                                   running, lod.camera_distance)
        else:
            pad = lod.padding if len(lod.padding) == 3 else (125, 136, 174)
            out += lod_struct.pack(len(entries), total_vertex, total_index,
                                   running, lod.camera_distance,
                                   lod.lod_level, lod.quality_level & 0xFF,
                                   pad[0] & 0xFF, pad[1] & 0xFF, pad[2] & 0xFF)
        for model, material_blob, vertex_blob, index_blob in entries:
            running += (_COMMON_HEADER.size + len(material_blob)
                        + len(vertex_blob) + len(index_blob))

    # Mesh sections.
    for lod, entries in zip(rmv.lods, encoded):
        for model, material_blob, vertex_blob, index_blob in entries:
            vertex_offset = _COMMON_HEADER.size + len(material_blob)
            index_offset = vertex_offset + len(vertex_blob)
            section_size = index_offset + len(index_blob)

            shader = model.shader_name.encode("utf-8", errors="replace")
            shader = shader[:12].ljust(12, b"\0")
            extra = (bytes(model.shader_extra) + b"\0" * 10)[:10]
            zero = (bytes(model.shader_zero) + b"\0" * 10)[:10]
            bbox_min, bbox_max = model.computed_bbox()

            out += _COMMON_HEADER.pack(
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
