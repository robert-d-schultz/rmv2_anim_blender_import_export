"""Reader/writer for Shogun 2's .variant_part_mesh (magic 'VMPF').

This is the skinned mesh format the game's unit parts are built from -
one file per helmet, torso, mask, saddle and so on, assembled at runtime
by the variant mesh definitions.  No public tool reads it: it is absent
from TheAssetEditor's C# reference and from RPFM, so everything here was
worked out from the 562 vanilla files and is checked the same way the
rest of this add-on is - every one of them must re-save byte for byte.

Container
---------

    +0   'VMPF'
    +4   version            0, 2 or 3
    +8   vertex format      0, 1 or 2 (see below)
    +12  attachment flag    0 or 1
    +16  part count         LOD count, or a part list for format 2
    +20  total vertices     )  CA writes 0 in two files; the per-part
    +24  total indices      )  counts are what the game actually uses
    +28  float param count
    +32  vec4 param count
    +36  parts, then the trailer

Each part is `u32 vertex_count, u32 index_count`, the vertex block, then
`index_count` u16 indices.  Vertex format 2 additionally prefixes every
part with its own name and material names.

The trailer holds the named material parameters (the same scheme as
.animatable_rigid_model), an optional attachment, and the fixed strings
that name the skeleton and the material set.

Skinning
--------

The interesting part.  A vertex carries up to two bone influences, and
its position is stored **once per influence, in that bone's own space** -
so reading a VMPF mesh means skinning it, there is no model-space copy.
The weight of the first influence is one byte; the second is the
remainder.  Single-influence vertices leave the second position, normal
and tangent frame zeroed, which is how the two cases are told apart.

Bone indices address the model's reference skeleton directly (the .anim
named in the trailer, e.g. 'man_shogun' -> shogun2_tpose.anim).  When
that skeleton is not available the bind pose is still recoverable from
the file alone: every two-influence vertex is one sample of the rigid
map between two bone spaces, so fitting those and spanning the resulting
graph places every bone that shares a vertex with the rest of the model.
See `bind_frames_from_geometry`.
"""

from __future__ import annotations

import struct
import heapq
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

MAGIC = b"VMPF"
SUPPORTED_VERSIONS = (0, 2, 3)

# Vertex format selector at +8.
VF_RIGID = 0            # stride 64, float32 positions, no skinning
VF_SKINNED = 1          # stride 48 (40 in version 0), two bone influences
VF_RIGID_NAMED = 2      # stride 64, each part prefixed with its own names

_STRIDE = {VF_RIGID: 64, VF_SKINNED: 48, VF_RIGID_NAMED: 64}
# Version 0 predates the second influence's tangent frame, so its skinned
# vertex is the same layout eight bytes shorter.
_SKINNED_STRIDE_V0 = 40

_HEADER = struct.Struct("<4s8I")
_HEADER_SIZE = _HEADER.size          # 36

_NAME_SIZE = 64                      # 32 UTF-16 characters
_PART_NAME_SIZE = 80                 # format 2 only
_ATTACH_NAME_SIZE = 32
_FLOAT_PARAM_SIZE = _NAME_SIZE + 4
_VEC4_PARAM_SIZE = _NAME_SIZE + 16
_ATTACH_SIZE = _ATTACH_NAME_SIZE + 64 + 4

# Trailer string slots for formats 0 and 1.  Versions 0 and 2 stop after
# the skeleton name; version 3 adds three material set names.
_TRAILER_SLOTS = {0: 1, 2: 1, 3: 4}


class VmpfError(Exception):
    pass


def _decode_fixed_utf16(raw: bytes) -> str:
    return raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]


def _encode_fixed_utf16(value: str, length: int) -> bytes:
    raw = value.encode("utf-16-le", errors="replace")[:length]
    if len(raw) % 2:                    # never split a UTF-16 code unit
        raw = raw[:-1]
    return raw.ljust(length, b"\0")


@dataclass
class VmpfPart:
    """One LOD (formats 0/1) or one named sub-part (format 2)."""
    vertices: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 0), np.uint8))
    indices: np.ndarray = field(
        default_factory=lambda: np.zeros(0, np.uint16))
    name: str = ""
    material_names: list = field(default_factory=list)
    # Vertex format 2 only: the attachment slot the prop hangs off, as a
    # signed index (-1 = none). Props sharing a value share a mount -
    # 1 is the weapon hand, 3 the ramrod/side-arm, 14 every "_bp"
    # backpack, 20 the sashimono banner poles.
    bone_index: int = -1

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)


@dataclass
class VmpfFile:
    version: int = 3
    vertex_format: int = VF_SKINNED
    parts: list = field(default_factory=list)
    skeleton_name: str = ""
    material_names: list = field(default_factory=list)
    float_params: list = field(default_factory=list)     # [(name, float)]
    vec4_params: list = field(default_factory=list)      # [(name, 4 floats)]
    attachment: Optional[tuple] = None   # (name, 4x4 row-major, bone index)
    # CA writes zero totals in two vanilla files even though the parts
    # hold real geometry; kept so those re-save unchanged.
    declared_totals: Optional[tuple] = None
    trailer_extra: bytes = b""
    # Set when the trailer does not re-encode to the bytes it was read
    # from - version 0 spells its material parameters differently from
    # every later file.  Rather than guess at a layout with one sample to
    # go on, the block is kept verbatim and written back unchanged.
    trailer_raw: Optional[bytes] = None

    @property
    def stride(self) -> int:
        if self.vertex_format == VF_SKINNED and self.version == 0:
            return _SKINNED_STRIDE_V0
        return _STRIDE[self.vertex_format]

    @property
    def is_skinned(self) -> bool:
        return self.vertex_format == VF_SKINNED


def load(data: bytes) -> VmpfFile:
    if len(data) < _HEADER_SIZE or data[:4] != MAGIC:
        raise VmpfError("not a .variant_part_mesh file (bad magic)")
    (_, version, vformat, attach_flag, part_count,
     total_vc, total_ic, float_count, vec4_count) = _HEADER.unpack_from(data)
    if version not in SUPPORTED_VERSIONS:
        raise VmpfError(f"unsupported .variant_part_mesh version {version}")
    if vformat not in _STRIDE:
        raise VmpfError(f"unknown vertex format {vformat}")

    vmpf = VmpfFile(version=version, vertex_format=vformat)
    stride = vmpf.stride
    off = _HEADER_SIZE

    for i in range(part_count):
        part = VmpfPart()
        if vformat == VF_RIGID_NAMED:
            part.name = _decode_fixed_utf16(data[off:off + _PART_NAME_SIZE])
            off += _PART_NAME_SIZE
            part.bone_index = struct.unpack_from("<i", data, off)[0]
            off += 4
            part.material_names = [
                _decode_fixed_utf16(data[off + n * _NAME_SIZE:
                                         off + (n + 1) * _NAME_SIZE])
                for n in range(3)]
            off += 3 * _NAME_SIZE
        if off + 8 > len(data):
            raise VmpfError(f"part {i} header runs past the end of the file")
        vc, ic = struct.unpack_from("<2I", data, off)
        off += 8
        vend = off + stride * vc
        iend = vend + 2 * ic
        if iend > len(data):
            raise VmpfError(
                f"part {i} claims {vc} vertices and {ic} indices, "
                "which runs past the end of the file")
        part.vertices = np.frombuffer(
            data, np.uint8, count=stride * vc, offset=off).reshape(vc, stride)
        part.indices = np.frombuffer(data, np.uint16, count=ic, offset=vend)
        if ic and int(part.indices.max()) >= vc:
            raise VmpfError(
                f"part {i} index {int(part.indices.max())} is out of range "
                f"for {vc} vertices")
        vmpf.parts.append(part)
        off = iend

    counted = (sum(p.vertex_count for p in vmpf.parts),
               sum(len(p.indices) for p in vmpf.parts))
    if (total_vc, total_ic) != counted:
        # Two vanilla files declare (0, 0); the parts are authoritative.
        vmpf.declared_totals = (total_vc, total_ic)

    _load_trailer(vmpf, data, off, attach_flag, float_count, vec4_count)
    return vmpf


def _load_trailer(vmpf: VmpfFile, data: bytes, off: int, attach_flag: int,
                  float_count: int, vec4_count: int) -> None:
    start = off
    _parse_trailer(vmpf, data, off, attach_flag, float_count, vec4_count)
    # Self-check: anything that does not survive a re-encode is kept as
    # bytes instead, so an unrecognised layout can never corrupt a file.
    if _encode_trailer(vmpf) != data[start:]:
        vmpf.trailer_raw = data[start:]


def _parse_trailer(vmpf: VmpfFile, data: bytes, off: int, attach_flag: int,
                   float_count: int, vec4_count: int) -> None:
    if attach_flag and vmpf.vertex_format != VF_RIGID_NAMED:
        name = _decode_fixed_utf16(data[off:off + _ATTACH_NAME_SIZE])
        matrix = struct.unpack_from("<16f", data, off + _ATTACH_NAME_SIZE)
        bone = struct.unpack_from("<I", data, off + _ATTACH_NAME_SIZE + 64)[0]
        vmpf.attachment = (name, matrix, bone)
        off += _ATTACH_SIZE

    for _ in range(float_count):
        name = _decode_fixed_utf16(data[off:off + _NAME_SIZE])
        value = struct.unpack_from("<f", data, off + _NAME_SIZE)[0]
        vmpf.float_params.append((name, value))
        off += _FLOAT_PARAM_SIZE
    for _ in range(vec4_count):
        name = _decode_fixed_utf16(data[off:off + _NAME_SIZE])
        value = struct.unpack_from("<4f", data, off + _NAME_SIZE)
        vmpf.vec4_params.append((name, value))
        off += _VEC4_PARAM_SIZE

    if vmpf.vertex_format == VF_RIGID_NAMED:
        # Format 2 names its parts up front, so all that follows the
        # params is the skeleton name - in an 80-byte field here, not the
        # 64-byte one the other formats use.
        vmpf.skeleton_name = _decode_fixed_utf16(
            data[off:off + _PART_NAME_SIZE])
        vmpf.trailer_extra = data[off + _PART_NAME_SIZE:]
        return

    slots = _TRAILER_SLOTS.get(vmpf.version, 1)
    names = []
    for n in range(slots):
        start = off + n * _NAME_SIZE
        if start + _NAME_SIZE > len(data):
            break
        names.append(_decode_fixed_utf16(data[start:start + _NAME_SIZE]))
    vmpf.skeleton_name = names[0] if names else ""
    vmpf.material_names = names[1:]
    vmpf.trailer_extra = data[off + len(names) * _NAME_SIZE:]


def save(vmpf: VmpfFile) -> bytes:
    stride = vmpf.stride
    counted = (sum(p.vertex_count for p in vmpf.parts),
               sum(len(p.indices) for p in vmpf.parts))
    totals = vmpf.declared_totals or counted

    out = bytearray(_HEADER.pack(
        MAGIC, vmpf.version, vmpf.vertex_format,
        1 if vmpf.attachment else 0, len(vmpf.parts),
        totals[0], totals[1],
        len(vmpf.float_params), len(vmpf.vec4_params)))

    for part in vmpf.parts:
        if part.vertices.size and part.vertices.shape[1] != stride:
            raise VmpfError(
                f"part vertex stride {part.vertices.shape[1]} does not match "
                f"the file's {stride}")
        if vmpf.vertex_format == VF_RIGID_NAMED:
            out += _encode_fixed_utf16(part.name, _PART_NAME_SIZE)
            out += struct.pack("<i", part.bone_index)
            names = list(part.material_names)[:3]
            names += [""] * (3 - len(names))
            for name in names:
                out += _encode_fixed_utf16(name, _NAME_SIZE)
        out += struct.pack("<2I", part.vertex_count, len(part.indices))
        out += np.ascontiguousarray(part.vertices, np.uint8).tobytes()
        out += np.ascontiguousarray(part.indices, np.uint16).tobytes()

    out += (vmpf.trailer_raw if vmpf.trailer_raw is not None
            else _encode_trailer(vmpf))
    return bytes(out)


def _encode_trailer(vmpf: VmpfFile) -> bytes:
    out = bytearray()
    if vmpf.attachment and vmpf.vertex_format != VF_RIGID_NAMED:
        name, matrix, bone = vmpf.attachment
        out += _encode_fixed_utf16(name, _ATTACH_NAME_SIZE)
        out += struct.pack("<16f", *matrix)
        out += struct.pack("<I", bone)

    for name, value in vmpf.float_params:
        out += _encode_fixed_utf16(name, _NAME_SIZE)
        out += struct.pack("<f", value)
    for name, value in vmpf.vec4_params:
        out += _encode_fixed_utf16(name, _NAME_SIZE)
        out += struct.pack("<4f", *value)

    if vmpf.vertex_format == VF_RIGID_NAMED:
        out += _encode_fixed_utf16(vmpf.skeleton_name, _PART_NAME_SIZE)
    else:
        slots = _TRAILER_SLOTS.get(vmpf.version, 1)
        names = [vmpf.skeleton_name] + list(vmpf.material_names)
        names = (names + [""] * slots)[:slots]
        for name in names:
            out += _encode_fixed_utf16(name, _NAME_SIZE)

    out += vmpf.trailer_extra
    return bytes(out)


# ---------------------------------------------------------------------------
# Vertex decoding
# ---------------------------------------------------------------------------
#
# Skinned vertex, stride 48 (stride 40 in version 0 stops at byte 40):
#
#      0..5   half3   position, influence 0, in bone 0's space
#      6..7   half    UV u
#      8..13  half3   position, influence 1 (zero when single-influence)
#     14..15  half    UV v
#     16..23  half4   constant (0, 0, 1, 1)
#     24..26  ubyte3  normal,   influence 0      stored z, y, x
#     27      ubyte   bone index 0
#     28..30  ubyte3  normal,   influence 1
#     31      ubyte   bone index 1
#     32..34  ubyte3  tangent,  influence 0
#     35      ubyte   weight of influence 0, /255
#     36..38  ubyte3  binormal, influence 0
#     39      ubyte   0
#     40..42  ubyte3  tangent,  influence 1
#     43      ubyte   0
#     44..46  ubyte3  binormal, influence 1
#     47      ubyte   0
#
# Rigid vertex, stride 64:
#
#      0..11  float3  position
#     12..15  float   0
#     16..18  ubyte3  normal (z, y, x)      19  0
#     20..22  ubyte3  tangent               23  0
#     24..26  ubyte3  binormal              27  0
#     28..35  float2  UV
#     36..43  8 bytes 0
#     44..47  ubyte4  colour
#     48..55  8 bytes 0
#     56..63  float2  constant (1.0, 1.0)


def _byte_vectors(raw: np.ndarray, offset: int) -> np.ndarray:
    """Decode a 3-byte direction.  The tangent frame is stored z, y, x."""
    v = raw[:, offset:offset + 3].astype(np.float32) / 127.5 - 1.0
    return v[:, ::-1]


def decode_vertices(vmpf: VmpfFile, part: VmpfPart) -> dict:
    """Split one part's vertex block into named channels.

    Skinned parts come back with per-influence positions and frames still
    in their own bone spaces - call `skin` to combine them.
    """
    raw = part.vertices
    n = len(raw)
    if vmpf.is_skinned:
        halfs = raw.view(np.float16).reshape(n, -1)
        out = {
            "position0": halfs[:, 0:3].astype(np.float32),
            "position1": halfs[:, 4:7].astype(np.float32),
            "uv": np.stack([halfs[:, 3], halfs[:, 7]], 1).astype(np.float32),
            "normal0": _byte_vectors(raw, 24),
            "normal1": _byte_vectors(raw, 28),
            "tangent0": _byte_vectors(raw, 32),
            "binormal0": _byte_vectors(raw, 36),
            "bone0": raw[:, 27].astype(np.int32),
            "bone1": raw[:, 31].astype(np.int32),
            "weight0": raw[:, 35].astype(np.float32) / 255.0,
        }
        if raw.shape[1] >= 48:
            out["tangent1"] = _byte_vectors(raw, 40)
            out["binormal1"] = _byte_vectors(raw, 44)
        else:
            out["tangent1"] = np.zeros((n, 3), np.float32)
            out["binormal1"] = np.zeros((n, 3), np.float32)
        return out

    floats = raw.view(np.float32).reshape(n, -1)
    return {
        "position0": floats[:, 0:3].copy(),
        "position_w": floats[:, 3].copy(),      # 0.0 or 1.0
        "uv": floats[:, 7:9].copy(),
        "uv1": floats[:, 9:11].copy(),          # zero in most files
        "normal0": _byte_vectors(raw, 16),
        "tangent0": _byte_vectors(raw, 20),
        "binormal0": _byte_vectors(raw, 24),
        "colour": raw[:, 44:48].astype(np.float32) / 255.0,
        "bone0": np.zeros(n, np.int32),
        "weight0": np.ones(n, np.float32),
    }


def encode_rigid_vertices(positions: np.ndarray, normals: np.ndarray,
                          tangents: np.ndarray, binormals: np.ndarray,
                          uv0: np.ndarray, uv1: np.ndarray,
                          colours: np.ndarray,
                          position_w: float = 0.0) -> np.ndarray:
    """Build a stride-64 rigid vertex block (vertex formats 0 and 2).

    Everything is in game space already; directions are written in the
    file's z,y,x byte order.  The trailing (1.0, 1.0) is constant in all
    66 vanilla rigid files, as are the eight zero bytes before it.
    """
    count = len(positions)
    raw = np.zeros((count, 64), np.uint8)
    floats = raw.view(np.float32).reshape(count, 16)
    floats[:, 0:3] = np.asarray(positions, np.float32)
    floats[:, 3] = position_w
    floats[:, 7:9] = np.asarray(uv0, np.float32)
    floats[:, 9:11] = np.asarray(uv1, np.float32)
    floats[:, 14:16] = 1.0
    raw[:, 16:19] = encode_direction(normals)
    raw[:, 20:23] = encode_direction(tangents)
    raw[:, 24:27] = encode_direction(binormals)
    raw[:, 44:48] = np.clip(
        np.round(np.asarray(colours, np.float32) * 255.0), 0, 255
    ).astype(np.uint8)
    return raw


def encode_direction(vectors: np.ndarray) -> np.ndarray:
    """(n,3) unit vectors -> (n,3) bytes, in the file's z,y,x order."""
    scaled = np.clip((np.asarray(vectors, np.float32) + 1.0) * 127.5,
                     0.0, 255.0)
    return np.round(scaled[:, ::-1]).astype(np.uint8)


def _apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def skin(channels: dict, frames: dict) -> tuple:
    """Combine per-influence data into model space.

    `frames` maps bone index -> 4x4 bind matrix.  Vertices whose bones are
    missing from `frames` are returned unmoved and flagged, so a caller
    can warn rather than silently misplacing geometry.

    Returns (positions, normals, placed) where `placed` is a bool mask.
    """
    p0 = channels["position0"]
    n = len(p0)
    weight = channels["weight0"][:, None]
    bone0 = channels["bone0"]
    positions = np.zeros((n, 3), np.float32)
    normals = np.zeros((n, 3), np.float32)
    placed = np.ones(n, bool)

    identity = np.eye(4, dtype=np.float32)
    unique0 = np.unique(bone0)
    for bone in unique0:
        mask = bone0 == bone
        matrix = frames.get(int(bone))
        if matrix is None:
            placed[mask] = False
            matrix = identity
        positions[mask] = _apply(matrix, p0[mask]) * weight[mask]
        normals[mask] = channels["normal0"][mask] @ matrix[:3, :3].T \
            * weight[mask]

    if "position1" not in channels:
        return positions, normals, placed

    second = channels["weight0"] < 1.0
    if not second.any():
        return positions, normals, placed
    bone1 = channels["bone1"]
    rest = 1.0 - channels["weight0"][:, None]
    for bone in np.unique(bone1[second]):
        mask = second & (bone1 == bone)
        matrix = frames.get(int(bone))
        if matrix is None:
            placed[mask] = False
            continue
        positions[mask] += _apply(matrix, channels["position1"][mask]) \
            * rest[mask]
        normals[mask] += channels["normal1"][mask] @ matrix[:3, :3].T \
            * rest[mask]
    return positions, normals, placed


# ---------------------------------------------------------------------------
# Recovering the bind pose without a skeleton
# ---------------------------------------------------------------------------


def _kabsch(source: np.ndarray, target: np.ndarray) -> tuple:
    """The rigid transform taking `source` onto `target`.

    Returns (4x4, residual) where the residual is the mean distance left
    over, relative to the spread of the points it was fitted from - so it
    is comparable between bone pairs of very different sizes, and large
    whenever the fit was underdetermined (e.g. three nearly collinear
    samples, which pin down no rotation about the line through them).
    """
    sc, tc = source.mean(0), target.mean(0)
    cov = (source - sc).T @ (target - tc)
    u, _, vt = np.linalg.svd(cov)
    flip = np.sign(np.linalg.det(vt.T @ u.T))
    rot = vt.T @ np.diag([1.0, 1.0, flip]) @ u.T
    out = np.eye(4, dtype=np.float64)
    out[:3, :3] = rot
    out[:3, 3] = tc - rot @ sc
    spread = np.linalg.norm(source - sc, axis=1).mean()
    error = np.linalg.norm(source @ rot.T + out[:3, 3] - target, axis=1)
    return out, float(error.mean() / max(spread, 1e-9))


def bind_frames_from_geometry(channels: dict, min_samples: int = 6,
                              max_residual: float = 0.02) -> dict:
    """Recover bone bind frames from the mesh alone.

    Each two-influence vertex states that one model point is at `p0` in
    bone0's space and at `p1` in bone1's, so the vertices sharing a bone
    pair determine the rigid map between those two spaces.  Treating
    bones as nodes and those maps as edges, spanning the graph places
    every bone in one component.

    Edge quality matters more than reach.  A pair with only a handful of
    nearly collinear samples fits a rotation that is barely constrained,
    and because every bone downstream of it inherits that error through
    the walk, one bad edge can wreck the whole model.  So edges are
    rejected outright above `max_residual`, and the walk is best-first on
    residual (Prim's algorithm) rather than breadth-first - each bone is
    reached through the most trustworthy route available.

    The result is in the root bone's space rather than the skeleton's, so
    the model may sit at an arbitrary rigid offset - fine for viewing,
    but prefer the real skeleton when one is available.  Bones that never
    share a well-determined edge with the rest are absent from the
    result; callers should report them rather than place them at random.
    """
    if "position1" not in channels:
        return {}
    p0, p1 = channels["position0"], channels["position1"]
    bone0, bone1 = channels["bone0"], channels["bone1"]
    second = np.nonzero(channels["weight0"] < 1.0)[0]
    if not len(second):
        return {}

    groups: dict = {}
    for i in second:
        groups.setdefault((int(bone0[i]), int(bone1[i])), []).append(i)

    adjacency: dict = {}
    weight_of: dict = {}
    for (a, b), rows in groups.items():
        if len(rows) < min_samples or a == b:
            continue
        rows = np.asarray(rows)
        matrix, residual = _kabsch(p0[rows].astype(np.float64),
                                   p1[rows].astype(np.float64))
        if not np.isfinite(residual) or residual > max_residual:
            continue
        # p1 = M @ p0, so bone b's frame is bone a's composed with M^-1.
        adjacency.setdefault(a, []).append((b, np.linalg.inv(matrix),
                                            residual))
        adjacency.setdefault(b, []).append((a, matrix, residual))
        weight_of[a] = weight_of.get(a, 0) + len(rows)
        weight_of[b] = weight_of.get(b, 0) + len(rows)
    if not adjacency:
        return {}

    root = max(adjacency, key=lambda bone: weight_of.get(bone, 0))
    frames = {root: np.eye(4, dtype=np.float32)}
    heap = [(residual, other, root)
            for other, _, residual in adjacency[root]]
    heapq.heapify(heap)
    while heap:
        _, other, via = heapq.heappop(heap)
        if other in frames:
            continue
        matrix = next(m for target, m, _ in adjacency[via] if target == other)
        frames[other] = (frames[via] @ matrix).astype(np.float32)
        for nxt, _, residual in adjacency[other]:
            if nxt not in frames:
                heapq.heappush(heap, (residual, nxt, other))
    return frames


def bind_frame_error(channels: dict, frames: dict) -> float:
    """How wrong a bind pose is, as a fraction of the model's size.

    A correct pose maps a two-influence vertex's two stored copies onto
    the same model-space point, so the largest gap between them measures
    the error directly.  Used to warn when a pose recovered from geometry
    could not be pinned down well enough to trust; a real skeleton scores
    essentially zero.  Returns 0.0 when there is nothing to compare.
    """
    if "position1" not in channels or not frames:
        return 0.0
    second = np.nonzero(channels["weight0"] < 1.0)[0]
    if not len(second):
        return 0.0
    bone0, bone1 = channels["bone0"], channels["bone1"]
    usable = [i for i in second
              if int(bone0[i]) in frames and int(bone1[i]) in frames]
    if not usable:
        return 0.0
    rows = np.asarray(usable)
    first = np.stack([_apply(frames[int(bone0[i])],
                             channels["position0"][i:i + 1])[0] for i in rows])
    other = np.stack([_apply(frames[int(bone1[i])],
                             channels["position1"][i:i + 1])[0] for i in rows])
    positions, _, placed = skin(channels, frames)
    if not placed.any():
        return 0.0
    extent = positions[placed].max(0) - positions[placed].min(0)
    diagonal = max(float(np.linalg.norm(extent)), 1e-9)
    return float(np.linalg.norm(first - other, axis=1).max() / diagonal)
