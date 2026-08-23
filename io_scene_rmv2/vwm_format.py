"""Reader/writer for Empire/Napoleon's .variant_weighted_mesh (VWM).

This is the skinned mesh format Empire's soldiers are built from - one
file per unit per LOD, holding every body part the unit's variants can
draw (head01, body02, legs01 and so on).

Like the rest of the pre-Rome 2 formats it is absent from
TheAssetEditor's C# reference and from RPFM, so everything here was
worked out from Empire's vanilla files and is held to the same bar as
the rest of this add-on: every file the game ships must re-save byte for
byte.  Shogun 2 ships exactly one .variant_weighted_mesh, which is why
this format was left alone until Empire; Empire ships 1269.

Container
---------

    u32         magic, always 0x12345678
    u32         version, always 1
    u32         float parameter count, then per parameter a name (u16
                *character* count + UTF-16LE) and one f32
    u32         vec4 parameter count, then name + 4 f32 each
    u32         part count, then per part:
                name (u16 character count + UTF-16LE), u32 vertex count,
                u32 index count
    then the geometry, one block per part in the same order:
        u32     vertex count again (it always agrees with the table)
        the vertices, see below
        u32     index count again
        that many u32 indices - note u32, as in .rigid_model, where
                .rigid_model_v2 uses u16
    then the attachment section, below - a lone zero when empty, which
                it is in all but six vanilla files

Unlike .animatable_rigid_model this container has no texture slots: the
part name doubles as the material/texture-set name, and the variant
system picks the textures at runtime.

A headerless variant also occurs (15 vanilla files, all unitmodels/).
It drops the magic, version and both parameter blocks and starts
straight at the part count; it is given the synthetic version 0 here,
the same trick anim_format uses for Shogun 2's headerless .anim.

Skinning
--------

The interesting part, and the same idea as Shogun 2's .variant_part_mesh:
a vertex carries **no model-space position at all**.  Instead it lists
one to eight bone influences, and stores its position *and* normal once
per influence, each in that bone's own space.  Reading a VWM mesh
therefore means skinning it, and the matching skeleton is not optional.

    f32[2]      uv
    f32[3]      tangent    ) in the first influence's bone space
    f32[3]      binormal   )
    u32         influence count, 1 to 8
    per influence:
        u32     bone index
        f32[3]  position, in that bone's space
        f32[3]  normal, in that bone's space
        f32     weight - the weights of a vertex sum to 1.0
    f32[4]      colour RGBA - see below

The colour block
----------------

Those last 16 bytes are zero in all 1305 vanilla files of both games, so
what they are cannot be read off directly.  Three things place them:

  * they are per *vertex*, not per influence.  45% of vanilla vertices
    have two influences and 0.4% have three or more, and a flat 16 bytes
    per vertex is what parses every one of those files to the byte;
  * they sit immediately after the tangent frame, which is exactly where
    the RGBA colour sits in a rigid-model vertex (arm_format's _COLOUR),
    and they are exactly its size;
  * CA's older testdata files - which are the same container before the
    colour channel existed - do not have them, and neither do the rigid
    objects in their attachment section (arm_format's version 0, 14
    floats).  Shipping files have the block in both sections at once.

So this is the same channel the rigid model gained when its vertex went
from 14 to 18 floats, arriving in the weighted mesh in the same revision.
Unlike the rigid model, though, no vanilla weighted mesh ever writes a
value into it, so the identification is by position and provenance
rather than by a file that uses it: treat the name as informed, and keep
whatever was read so files round-trip either way.

Older layouts
-------------

CA's testdata/ folders keep three earlier generations of this container.
They are read by trying each shape and keeping the one that consumes the
file exactly - the same trick arm_format uses for its bone indices:

    part table   vertex head        colour block
    nameless     uv                 no      (2 files)
    nameless     uv+tangent frame   no      (20 files)
    named        uv+tangent frame   no      (4 files)
    named        uv+tangent frame   yes     everything that shipped

The nameless table is a three-word header - part count, total vertices,
total indices - followed by the part bodies back to back, with no names
anywhere.  Those files also skin more heavily than anything that
shipped, up to 11 influences on a vertex where shipping files stop at 8.

Attachments
-----------

After the last part comes a second section: the props a unit carries.

    u32         attachment count - zero in all but six vanilla files
    per attachment:
        u32     magic, u32 version  ) absent in the headerless variant
        string  name, e.g. "rigid_equip_euro_cutlass01"
        u32     bone index it hangs off
        the rest is one .animatable_rigid_model object exactly as
        arm_format reads it: texture slots, material parameters,
        vertices, indices

Empire and Napoleon both ship unitmodels/euro_equipment.variant_weighted_mesh,
which is nothing but 134 of these - every musket, spear and flagpole in
the game - with no skinned parts at all.

The tangent and binormal were identified by rebuilding the tangent frame
from each triangle's bone-local positions and UVs and correlating: the
stored vectors line up with the UV-derived tangent and binormal
respectively (mean dot +0.87 and +0.90, cross terms ~0.00), which also
places them in bone space rather than model space.

Bone indices address the unit's skeleton, the .anim under
animations/battle/ that the unit's variant definition names.

Coordinates are in the game's space (right-handed, Y-up); conversion to
Blender space happens elsewhere.  All data is little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:
    from . import arm_format as af
except ImportError:                 # imported flat, as the tests do
    import arm_format as af

VWM_MAGIC = 0x12345678
SUPPORTED_VERSIONS = (1,)
# The headerless variant has no version field of its own; it is given
# this synthetic one so a loaded file always reports something and
# save() can put the bytes back exactly as they were.
NO_HEADER_VERSION = 0

# The most influences any shipping vertex has, and so the most an export
# should produce.
MAX_INFLUENCES = 8
# The reader is looser: CA's own testdata files go up to 11, and past
# this an influence count means we have lost our place in the stream
# rather than found an exotic file.
_MAX_READ_INFLUENCES = 16

# Per-vertex RGBA colour, in floats and in bytes.  See the module
# docstring for how this block was identified.
_COLOUR_FLOATS = 4
_COLOUR_BYTES = _COLOUR_FLOATS * 4


class VwmFormatError(Exception):
    """Raised when a file cannot be parsed/serialized."""


@dataclass
class VwmPart:
    """One drawable part: its geometry plus the name that selects its
    textures.

    The per-influence data is stored flat rather than as a list of lists,
    so that influence_counts says how many entries of each influence_*
    array belong to each vertex, in vertex order.  influence_offsets
    gives the start of each vertex's run.
    """
    name: str = ""

    uv: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 2), np.float32))
    tangents: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    binormals: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    # Zero in every vanilla file - see the module docstring.
    colours: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 4), np.float32))

    influence_counts: np.ndarray = field(
        default_factory=lambda: np.zeros((0,), np.int32))
    influence_bones: np.ndarray = field(
        default_factory=lambda: np.zeros((0,), np.uint32))
    influence_positions: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    influence_normals: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    influence_weights: np.ndarray = field(
        default_factory=lambda: np.zeros((0,), np.float32))

    indices: np.ndarray = field(
        default_factory=lambda: np.zeros((0,), np.uint32))

    @property
    def vertex_count(self) -> int:
        return len(self.influence_counts)

    @property
    def influence_offsets(self) -> np.ndarray:
        """Start of each vertex's run within the flat influence arrays."""
        out = np.zeros(len(self.influence_counts) + 1, np.int64)
        np.cumsum(self.influence_counts, out=out[1:])
        return out

    def bones_used(self) -> list:
        return sorted(set(int(b) for b in self.influence_bones))

    def vertex_influences(self, index: int) -> list:
        """[(bone, position, normal, weight)] for one vertex - convenient
        for callers that would rather not slice the flat arrays."""
        offsets = self.influence_offsets
        lo, hi = int(offsets[index]), int(offsets[index + 1])
        return [(int(self.influence_bones[i]),
                 self.influence_positions[i],
                 self.influence_normals[i],
                 float(self.influence_weights[i])) for i in range(lo, hi)]


@dataclass
class VwmAttachment:
    """A prop bolted to one bone: a weapon, a backpack, a flagpole.

    The geometry is an ordinary .animatable_rigid_model object, so it is
    kept as one and the Blender side can treat it like any other.
    """
    name: str = ""
    bone: int = 0
    mesh: "af.ArmMesh" = field(default_factory=af.ArmMesh)


@dataclass
class VwmFile:
    version: int = 1
    float_params: list = field(default_factory=list)   # [(name, float)]
    vec4_params: list = field(default_factory=list)    # [(name, (x,y,z,w))]
    parts: list = field(default_factory=list)          # [VwmPart]
    attachments: list = field(default_factory=list)    # [VwmAttachment]

    # Which of the four layouts in the module docstring this file uses.
    # The shipping one is all three; older files drop them from the
    # right.
    has_names: bool = True
    has_tangent_frame: bool = True
    has_colour: bool = True

    # The oldest, nameless files stop dead after the last part - there
    # is no attachment count to read, not even a zero.
    has_attachment_section: bool = True
    # Set when the attachment section could not be decoded, so that a
    # file we only half understand still re-saves unchanged.
    attachments_raw: Optional[bytes] = None

    @property
    def has_header(self) -> bool:
        return self.version != NO_HEADER_VERSION


# ---------------------------------------------------------------------------
# Reader plumbing
# ---------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.off = 0

    def _need(self, size: int):
        if self.off + size > len(self.data):
            raise VwmFormatError(
                f"Unexpected end of file at offset {self.off} (need "
                f"{size} more bytes, file is {len(self.data)})")

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        self._need(size)
        out = struct.unpack_from(fmt, self.data, self.off)
        self.off += size
        return out

    def u32(self) -> int:
        return self.unpack("<I")[0]

    def string(self) -> str:
        """u16 character count followed by that many UTF-16LE units."""
        count = self.unpack("<H")[0]
        self._need(count * 2)
        raw = self.data[self.off:self.off + count * 2]
        self.off += count * 2
        return raw.decode("utf-16-le")

    def bytes_(self, count: int) -> bytes:
        self._need(count)
        out = self.data[self.off:self.off + count]
        self.off += count
        return out

    @property
    def remaining(self) -> int:
        return len(self.data) - self.off


def _read_params(r: _Reader) -> tuple:
    float_count = r.u32()
    if float_count > 10000:
        raise VwmFormatError(
            f"Implausible float parameter count {float_count}")
    float_params = []
    for _ in range(float_count):
        name = r.string()
        float_params.append((name, r.unpack("<f")[0]))

    vec4_count = r.u32()
    if vec4_count > 10000:
        raise VwmFormatError(f"Implausible vec4 parameter count {vec4_count}")
    vec4_params = []
    for _ in range(vec4_count):
        name = r.string()
        vec4_params.append((name, r.unpack("<4f")))
    return float_params, vec4_params


def _read_vertices(r: _Reader, part: VwmPart, count: int,
                   tangent_frame: bool, colour: bool):
    """Read `count` vertices into `part`.

    The per-vertex influence count makes the stride variable, so this
    walks the block one vertex at a time rather than reshaping it.
    """
    head_floats = 8 if tangent_frame else 2
    trailer = _COLOUR_BYTES if colour else 0

    uv = np.empty((count, 2), np.float32)
    tangents = np.zeros((count, 3), np.float32)
    binormals = np.zeros((count, 3), np.float32)
    colours = np.zeros((count, 4), np.float32)
    counts = np.empty(count, np.int32)

    bones, positions, normals, weights = [], [], [], []
    data = r.data
    off = r.off
    end = len(data)

    for v in range(count):
        if off + head_floats * 4 + 4 > end:
            raise VwmFormatError(
                f"Unexpected end of file in vertex {v} of "
                f"{count} at offset {off}")
        head = struct.unpack_from(f"<{head_floats}f", data, off)
        uv[v] = head[0:2]
        if tangent_frame:
            tangents[v] = head[2:5]
            binormals[v] = head[5:8]
        off += head_floats * 4

        n = struct.unpack_from("<I", data, off)[0]
        off += 4
        if not 1 <= n <= _MAX_READ_INFLUENCES:
            raise VwmFormatError(
                f"Vertex {v} claims {n} bone influences (expected 1 to "
                f"{_MAX_READ_INFLUENCES}) - the vertex block is out of step")
        counts[v] = n

        if off + n * 32 + trailer > end:
            raise VwmFormatError(
                f"Unexpected end of file in vertex {v} of {count}")
        for _ in range(n):
            bones.append(struct.unpack_from("<I", data, off)[0])
            body = struct.unpack_from("<7f", data, off + 4)
            positions.append(body[0:3])
            normals.append(body[3:6])
            weights.append(body[6])
            off += 32

        if colour:
            colours[v] = struct.unpack_from("<4f", data, off)
            off += trailer

    part.colours = colours

    r.off = off
    part.uv = uv
    part.tangents = tangents
    part.binormals = binormals
    part.influence_counts = counts
    part.influence_bones = np.array(bones, np.uint32)
    part.influence_positions = np.array(
        positions, np.float32).reshape(-1, 3)
    part.influence_normals = np.array(normals, np.float32).reshape(-1, 3)
    part.influence_weights = np.array(weights, np.float32)


# The shapes an older file can have, most recent first.  A file is read
# by trying them in turn and keeping the first that consumes it exactly
# - the wrong shape derails within a vertex or two, so in practice only
# one ever fits.  See the module docstring.
_SHAPES = (
    (True, True, True),        # names, tangent frame, colour: everything
    (True, True, False),       #                               that shipped
    (False, True, False),
    (False, False, False),
)


def load(data: bytes) -> VwmFile:
    """Parse a .variant_weighted_mesh, header-bearing or headerless."""
    first = None
    for shape in _SHAPES:
        try:
            return _load(data, *shape)
        except (VwmFormatError, ValueError, struct.error) as exc:
            # A wrong shape derails in whatever way the bytes happen to
            # allow - a bad count, a string that is not UTF-16, a read
            # past the end - so any of those just means "not this one".
            if first is None:
                first = exc
    if isinstance(first, VwmFormatError):
        raise first
    raise VwmFormatError(
        f"Not a readable .variant_weighted_mesh: {first}") from first


def _load(data: bytes, has_names: bool, tangent_frame: bool,
          colour: bool) -> VwmFile:
    vwm = VwmFile(has_names=has_names, has_tangent_frame=tangent_frame,
                  has_colour=colour)
    r = _Reader(data)

    magic = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else 0
    if magic == VWM_MAGIC:
        r.off = 4
        vwm.version = r.u32()
        if vwm.version not in SUPPORTED_VERSIONS:
            raise VwmFormatError(
                f"Unsupported .variant_weighted_mesh version "
                f"{vwm.version} (supported: {SUPPORTED_VERSIONS})")
        vwm.float_params, vwm.vec4_params = _read_params(r)
    else:
        # No magic?  Then the file opens with the part count, and the
        # part table that follows is what has to make sense.
        vwm.version = NO_HEADER_VERSION

    part_count = r.u32()
    # A part count of zero is legitimate with a header - Empire ships one
    # such placeholder (unitmodels/euro_equipment) - but a headerless
    # file that opens with a zero has told us nothing we can check, so
    # treat that as "not a VWM" rather than as an empty model.
    if part_count > 4096 or (part_count == 0 and not vwm.has_header):
        raise VwmFormatError(
            f"Implausible part count {part_count} - is this really a "
            f".variant_weighted_mesh?")

    table = []
    if has_names:
        for i in range(part_count):
            name = r.string()
            vertex_count, index_count = r.unpack("<II")
            if index_count % 3:
                raise VwmFormatError(
                    f"Part {i} ({name!r}) has {index_count} indices, which "
                    f"is not a whole number of triangles")
            table.append((name, vertex_count, index_count))
        totals = None
    else:
        # The nameless header gives the totals instead of a per-part
        # table, so the part bodies are what says where each one ends.
        totals = r.unpack("<II")
        table = [("", None, None)] * part_count

    for i, (name, vertex_count, index_count) in enumerate(table):
        part = VwmPart(name=name)
        block_vertices = r.u32()
        if vertex_count is None:
            vertex_count = block_vertices
            if vertex_count > 1000000:
                raise VwmFormatError(
                    f"Part {i}: implausible vertex count {vertex_count}")
        elif block_vertices != vertex_count:
            raise VwmFormatError(
                f"Part {i} ({name!r}): the table says {vertex_count} "
                f"vertices but the block says {block_vertices}")
        _read_vertices(r, part, vertex_count, tangent_frame, colour)

        block_indices = r.u32()
        if index_count is None:
            index_count = block_indices
            if index_count > 3000000 or index_count % 3:
                raise VwmFormatError(
                    f"Part {i}: implausible index count {index_count}")
        elif block_indices != index_count:
            raise VwmFormatError(
                f"Part {i} ({name!r}): the table says {index_count} "
                f"indices but the block says {block_indices}")
        part.indices = np.frombuffer(
            r.bytes_(index_count * 4), dtype="<u4").copy()
        if vertex_count and index_count and part.indices.max() >= vertex_count:
            raise VwmFormatError(
                f"Part {i} ({name!r}): index {int(part.indices.max())} is "
                f"out of range for {vertex_count} vertices")
        vwm.parts.append(part)

    if totals is not None:
        counted = (sum(p.vertex_count for p in vwm.parts),
                   sum(len(p.indices) for p in vwm.parts))
        if counted != totals:
            raise VwmFormatError(
                f"The header totals {totals} do not match the "
                f"{counted} the parts add up to")

    _read_attachments(vwm, r)
    if r.remaining:
        raise VwmFormatError(
            f"{r.remaining} unparsed bytes at the end of the file")
    return vwm


def _read_attachments(vwm: VwmFile, r: _Reader):
    """The props section that closes the file.

    Each entry is a name and a bone index in front of an ordinary
    .animatable_rigid_model object, so arm_format does the real work.
    Anything that will not decode is kept as raw bytes rather than
    failing the file: this section is empty in all but six vanilla
    files, and an unreadable one still has to re-save unchanged.
    """
    if not r.remaining:
        # The nameless generation predates the section entirely.
        vwm.has_attachment_section = False
        return

    start = r.off
    # arm_format reads the objects, so hand it the reader it expects and
    # pick the offset back up afterwards.
    ar = af.make_reader(r.data, r.off)
    try:
        count = ar.u32()
        if count > 4096:
            raise VwmFormatError(f"Implausible attachment count {count}")
        # Same tell as arm_format: no magic where the first object
        # starts means the older, headerless objects.
        headerless = count and ar.remaining >= 4 and struct.unpack_from(
            "<I", ar.data, ar.off)[0] != af.ARM_MAGIC
        for i in range(count):
            version = af.NO_HEADER_VERSION
            if not headerless:
                magic = ar.u32()
                if magic != af.ARM_MAGIC:
                    raise VwmFormatError(
                        f"Attachment {i}: bad magic 0x{magic:08x}")
                version = ar.u32()
                if version not in af.SUPPORTED_VERSIONS:
                    raise VwmFormatError(
                        f"Attachment {i}: unsupported object version "
                        f"{version}")
            # The bone comes in front of the object here, so the
            # object itself has no trailing bone index of its own.
            entry = VwmAttachment(name=ar.string(), bone=ar.u32(),
                                  mesh=af.ArmMesh(version=version,
                                                  bone_index=None))
            af.read_object_body(ar, entry.mesh, i,
                                texture_block=not headerless)
            vwm.attachments.append(entry)
        r.off = ar.off
    except (VwmFormatError, af.ArmFormatError, struct.error, ValueError):
        vwm.attachments = []
        r.off = start
        vwm.attachments_raw = r.bytes_(r.remaining)


# ---------------------------------------------------------------------------
# Skinning
# ---------------------------------------------------------------------------

def vertex_index_per_influence(part: VwmPart) -> np.ndarray:
    """Which vertex each row of the flat influence arrays belongs to."""
    return np.repeat(np.arange(part.vertex_count, dtype=np.int64),
                     np.asarray(part.influence_counts, np.int64))


def skin(part: VwmPart, frames: dict) -> tuple:
    """Combine the per-influence, per-bone-space data into model space.

    `frames` maps bone index -> 4x4 game-space bind matrix.  A vertex is
    the weighted sum of its position seen through each of its bones, and
    likewise for its normal (rotation only, so no translation).

    Vertices naming a bone that `frames` does not have are flagged in the
    returned mask rather than silently misplaced, so the caller can warn.

    Returns (positions, normals, placed).
    """
    count = part.vertex_count
    positions = np.zeros((count, 3), np.float32)
    normals = np.zeros((count, 3), np.float32)
    placed = np.ones(count, bool)
    if not count or not len(part.influence_bones):
        return positions, normals, placed

    owner = vertex_index_per_influence(part)
    bones = np.asarray(part.influence_bones)
    weights = np.asarray(part.influence_weights, np.float32)[:, None]

    for bone in np.unique(bones):
        rows = bones == bone
        matrix = frames.get(int(bone))
        if matrix is None:
            placed[owner[rows]] = False
            continue
        rotation = np.asarray(matrix, np.float32)[:3, :3]
        offset = np.asarray(matrix, np.float32)[:3, 3]
        moved = part.influence_positions[rows] @ rotation.T + offset
        turned = part.influence_normals[rows] @ rotation.T
        np.add.at(positions, owner[rows], moved * weights[rows])
        np.add.at(normals, owner[rows], turned * weights[rows])

    return positions, normals, placed


def influence_table(part: VwmPart) -> tuple:
    """The influences as dense (vertex count, max influences) arrays.

    Blender wants one weight per (vertex, bone) pair, so the ragged runs
    are padded out to a rectangle; padding rows carry weight 0 and are
    ignored by mesh_build.add_vertex_groups.
    """
    count = part.vertex_count
    counts = np.asarray(part.influence_counts, np.int64)
    width = int(counts.max()) if count and len(counts) else 0
    if not count or not width:
        return (np.zeros((count, 1), np.int64),
                np.zeros((count, 1), np.float32))

    bones = np.zeros((count, width), np.int64)
    weights = np.zeros((count, width), np.float32)
    owner = vertex_index_per_influence(part)
    # Position of each influence within its own vertex's run.
    starts = np.zeros(count + 1, np.int64)
    np.cumsum(counts, out=starts[1:])
    slot = np.arange(len(owner), dtype=np.int64) - starts[owner]
    bones[owner, slot] = np.asarray(part.influence_bones, np.int64)
    weights[owner, slot] = np.asarray(part.influence_weights, np.float32)
    return bones, weights


def build_influences(counts, bones, positions, normals, weights) -> dict:
    """Pack per-vertex influence lists into the flat arrays a VwmPart
    holds.  `counts` is per vertex; the rest are flat, in vertex order."""
    return {
        "influence_counts": np.asarray(counts, np.int32),
        "influence_bones": np.asarray(bones, np.uint32),
        "influence_positions": np.asarray(
            positions, np.float32).reshape(-1, 3),
        "influence_normals": np.asarray(normals, np.float32).reshape(-1, 3),
        "influence_weights": np.asarray(weights, np.float32),
    }


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _write_string(out: bytearray, text: str):
    raw = text.encode("utf-16-le")
    out += struct.pack("<H", len(raw) // 2)
    out += raw


def _write_params(out: bytearray, vwm: VwmFile):
    out += struct.pack("<I", len(vwm.float_params))
    for name, value in vwm.float_params:
        _write_string(out, name)
        out += struct.pack("<f", value)
    out += struct.pack("<I", len(vwm.vec4_params))
    for name, value in vwm.vec4_params:
        _write_string(out, name)
        out += struct.pack("<4f", *value)


def save(vwm: VwmFile) -> bytes:
    """Serialize back to bytes.  Round-trips vanilla files exactly."""
    out = bytearray()
    if vwm.has_header:
        if vwm.version not in SUPPORTED_VERSIONS:
            raise VwmFormatError(
                f"Cannot write .variant_weighted_mesh version "
                f"{vwm.version} (supported: {SUPPORTED_VERSIONS})")
        out += struct.pack("<II", VWM_MAGIC, vwm.version)
        _write_params(out, vwm)

    out += struct.pack("<I", len(vwm.parts))
    if vwm.has_names:
        for part in vwm.parts:
            _write_string(out, part.name)
            out += struct.pack("<II", part.vertex_count, len(part.indices))
    else:
        out += struct.pack("<II",
                           sum(p.vertex_count for p in vwm.parts),
                           sum(len(p.indices) for p in vwm.parts))

    for index, part in enumerate(vwm.parts):
        counts = part.influence_counts
        total = int(counts.sum())
        for name, array in (
                ("influence_bones", part.influence_bones),
                ("influence_positions", part.influence_positions),
                ("influence_normals", part.influence_normals),
                ("influence_weights", part.influence_weights)):
            if len(array) != total:
                raise VwmFormatError(
                    f"Part {index} ({part.name!r}): {name} holds "
                    f"{len(array)} entries but the influence counts add "
                    f"up to {total}")

        out += struct.pack("<I", part.vertex_count)

        uv = np.asarray(part.uv, np.float32)
        tangents = np.asarray(part.tangents, np.float32)
        binormals = np.asarray(part.binormals, np.float32)
        bones = np.asarray(part.influence_bones, np.uint32)
        positions = np.asarray(part.influence_positions, np.float32)
        normals = np.asarray(part.influence_normals, np.float32)
        weights = np.asarray(part.influence_weights, np.float32)

        colours = np.asarray(part.colours, np.float32)
        if vwm.has_colour and len(colours) != part.vertex_count:
            if colours.size:
                raise VwmFormatError(
                    f"Part {index} ({part.name!r}): colours holds "
                    f"{len(colours)} entries but there are "
                    f"{part.vertex_count} vertices")
            colours = np.zeros((part.vertex_count, 4), np.float32)

        cursor = 0
        for v in range(part.vertex_count):
            if vwm.has_tangent_frame:
                out += struct.pack("<8f", uv[v][0], uv[v][1],
                                   *tangents[v], *binormals[v])
            else:
                out += struct.pack("<2f", uv[v][0], uv[v][1])
            n = int(counts[v])
            if not 1 <= n <= _MAX_READ_INFLUENCES:
                raise VwmFormatError(
                    f"Part {index} ({part.name!r}): vertex {v} has {n} "
                    f"influences (expected 1 to {_MAX_READ_INFLUENCES})")
            out += struct.pack("<I", n)
            for k in range(cursor, cursor + n):
                out += struct.pack("<I", int(bones[k]))
                out += struct.pack("<7f", *positions[k], *normals[k],
                                   float(weights[k]))
            cursor += n
            if vwm.has_colour:
                out += struct.pack("<4f", *colours[v])

        out += struct.pack("<I", len(part.indices))
        out += np.asarray(part.indices, "<u4").tobytes()

    if vwm.attachments_raw is not None:
        out += vwm.attachments_raw
        return bytes(out)

    if not vwm.has_attachment_section:
        if vwm.attachments:
            raise VwmFormatError(
                "This file's layout has no attachment section, so its "
                f"{len(vwm.attachments)} attachment(s) cannot be written")
        return bytes(out)

    out += struct.pack("<I", len(vwm.attachments))
    for i, entry in enumerate(vwm.attachments):
        if entry.mesh.version != af.NO_HEADER_VERSION:
            out += struct.pack("<II", af.ARM_MAGIC, entry.mesh.version)
        _write_string(out, entry.name)
        out += struct.pack("<I", entry.bone)
        af.write_object_body(
            out, entry.mesh, i,
            texture_block=entry.mesh.version != af.NO_HEADER_VERSION)
    return bytes(out)
