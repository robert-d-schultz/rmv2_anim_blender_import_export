"""Binary reader/writer for Creative Assembly's .animatable_rigid_model
and .rigid_model formats (Shogun 2, Empire and Napoleon).

Like rmv2_format and anim_format, this module is free of any Blender (bpy)
dependencies so it can be unit-tested with a plain Python interpreter.

The format has no C# reference in TheAssetEditor - it was reverse
engineered from the vanilla files of all three games (Shogun 2's 473 to
begin with, then Empire's 4266 and Napoleon's 4913) and cross-checked
against the user's standalone exporter script:

    u32         object count
    per object:
        u32     magic, always 0x12345678   ) absent in the headerless
        u32     version (1 to 5)           ) variant - see below
        the texture slots, always in the order diffuse, normal, gloss,
                ao, with older versions simply stopping early.  A slot is
                a u16 *character* count followed by UTF-16LE, optionally
                preceded by a one-byte flag:
                    version 1 and headerless: 1 slot,  no flags
                    version 2:                3 slots, all flagged
                    version 3 and up:         4 slots, first 3 flagged
                In .animatable_rigid_model the flag is always 0 and the
                name is a bare texture set; .rigid_model also uses 1 (and
                once 4), where the name tends to be a full path - so the
                flag is kept rather than asserted away.
        version >= 4 only:
        u32     float parameter count, then per parameter a name (u16
                character count + UTF-16LE) and one f32.  Vanilla files
                have 0, 11 or 13 of these ("light_scale", "bumpfactor"...)
        u32     vec4 parameter count, then name + 4 f32 each ("specfactor")
        u32     vertex count, then vertex_count * 20 float32:
                position (3), normal (3), uv (2), tangent (3),
                binormal (3), colour RGBA (4), uv2 (2).
                The older objects drop channels off the end and keep
                everything before them at the same offset: versions 1
                and 2 have no uv2 (18 floats), and the headerless
                variant has no colour either (14 floats).
        u32     index count, then that many u32 indices - note u32, where
                .rigid_model_v2 uses u16
        u32     bone index this object is rigidly attached to -
                .animatable_rigid_model only
    finally, .rigid_model only:
    6 f32       the model's overall bounding box (min xyz, max xyz)

An ARM file is a flat list of objects rather than a LOD tree: every object
is welded to exactly one bone of the matching .anim skeleton, which is how
these games animate siege engines and other jointed props.  There is no
per-vertex skinning here at all.

Every version adds exactly one thing to the one before it, which is what
lets a single reader cover them all:

    version  texture slots   params   vertex
    0        1, unflagged    no       14 floats
    1        1, unflagged    no       18  (+ colour)
    2        3, flagged      no       18
    3        4 (3 flagged)   no       20  (+ uv2)
    4, 5     4 (3 flagged)   yes      20

Version 1 is the Empire/Napoleon-era object, used for campaign mountains,
the flagpole, the boarding plank and one naval cannon - 23 files across
the two games, 12 of them distinct (Napoleon ships the same set bar the
cannon).  Version 2 sits between it and 3, and only CA's testdata/fence
models use it.  Both carry bone indices in the animatable form just as
the later versions do.

Older still is a *headerless* object that drops the per-object magic and
version entirely, so the file's object count is followed straight by the
first name.  It is detected by the absence of the magic where the first
object should start, and given the synthetic version 0 - the same trick
anim_format and vwm_format use for their own headerless variants.  One
vanilla file uses it (enginemodels/cannon_test_model), plus five of CA's
testdata ones.  A file is headerless throughout or not at all.

Losing the magic also costs the reader its landmark for the *bone index*
lookahead below, so for those files whether the objects carry one is
decided per file rather than per object: both readings are tried and the
one that consumes the file exactly is kept.  Vanilla has examples either
way (cannon_test_model has bone indices, testdata's loki and victory_test
do not), and a mixed file could not be read back at all.

`.rigid_model` is the same container without that trailing bone index -
that is the whole of what "animatable" adds.  It is the Empire/Napoleon
era format still present in Shogun 2 for scenery and props that never
move.  Since the two are otherwise identical, one reader
handles both: whether an object carries a bone index is decided by looking
ahead for the next object's magic (or end of file) rather than by
trusting the file's extension.  `.rigid_model` also closes the file with a
bounding box, which the animatable form has no equivalent for.

Coordinates are in the game's space (right-handed, Y-up); conversion to
Blender space happens elsewhere.  All data is little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

ARM_MAGIC = 0x12345678
# Every object version round-trips byte-identically across every vanilla
# file of Shogun 2, Empire and Napoleon.
SUPPORTED_VERSIONS = (1, 2, 3, 4, 5)

# One vanilla file (enginemodels/cannon_test_model.animatable_rigid_model,
# plus three of CA's testdata ones) drops the per-object magic and version
# entirely: the object count is followed straight by the first name.  It
# is given this synthetic version, the same trick anim_format and
# vwm_format use for their headerless variants.  Its vertex is shorter
# again - no colour and no second UV set.
NO_HEADER_VERSION = 0

# Vertex channel offsets within the 20-float stride.
VERTEX_FLOATS = 20
_POS = slice(0, 3)
_NORMAL = slice(3, 6)
_UV = slice(6, 8)
_TANGENT = slice(8, 11)
_BINORMAL = slice(11, 14)
_COLOUR = slice(14, 18)
_UV2 = slice(18, 20)

# Versions 1 and 2 are the same vertex minus the second UV set, so their
# stride is two floats shorter and everything before uv2 sits at the same
# offset.
V1_VERTEX_FLOATS = 18
# The headerless object drops the colour block as well, leaving position,
# normal, uv and the tangent frame.
V0_VERTEX_FLOATS = 14

TEXTURE_SLOTS = ("diffuse", "normal", "gloss", "ao")

# 6 float32: the bounding box .rigid_model appends after the last object.
_BBOX_SIZE = 24

# Named material parameter blocks first appear in object version 4.
_PARAMS_FROM_VERSION = 4


def vertex_floats(version: int) -> int:
    """The stride, in float32, of one vertex of this object version."""
    if version == NO_HEADER_VERSION:
        return V0_VERTEX_FLOATS
    if version in (1, 2):
        return V1_VERTEX_FLOATS
    return VERTEX_FLOATS


def texture_layout(version: int) -> tuple:
    """(how many texture slots this version has, how many of them carry a
    leading flag byte).

    The four are always in TEXTURE_SLOTS order, so a version with fewer
    simply stops early: version 2 has no ao slot, and the two oldest
    carry the bare texture set alone.
    """
    if version in (NO_HEADER_VERSION, 1):
        return 1, 0
    if version == 2:
        return 3, 3
    return 4, 3


class ArmFormatError(Exception):
    """Raised when a file cannot be parsed/serialized."""


@dataclass
class ArmMesh:
    """One object: geometry plus the material that draws it."""
    version: int = 5
    # Bare texture names in TEXTURE_SLOTS order, "" where unused.  Vanilla
    # files always leave the ao slot empty.
    textures: list = field(default_factory=lambda: ["", "", "", ""])
    # One flag byte per texture slot (the 4th slot has none, so only the
    # first three are used). 0 throughout .animatable_rigid_model; 1 and 4
    # also occur in .rigid_model. Kept so files re-save unchanged.
    texture_flags: list = field(default_factory=lambda: [0, 0, 0])
    float_params: list = field(default_factory=list)   # [(name, float)]
    vec4_params: list = field(default_factory=list)    # [(name, (x,y,z,w))]
    # None means the object has no bone field at all, which is what makes
    # a file a plain .rigid_model rather than an animatable one.
    bone_index: Optional[int] = 0

    positions: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    normals: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    uv0: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 2), np.float32))
    uv1: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 2), np.float32))
    tangents: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    binormals: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    colours: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 4), np.float32))
    indices: np.ndarray = field(
        default_factory=lambda: np.zeros((0,), np.uint32))

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    def get_texture(self, slot: str) -> str:
        return self.textures[TEXTURE_SLOTS.index(slot)]

    def set_texture(self, slot: str, name: str):
        self.textures[TEXTURE_SLOTS.index(slot)] = name

    def get_float_param(self, name: str):
        for key, value in self.float_params:
            if key == name:
                return value
        return None


@dataclass
class ArmFile:
    meshes: list = field(default_factory=list)      # [ArmMesh]
    # .rigid_model closes with the model's overall bounding box as 6
    # float32 (min xyz, max xyz). .animatable_rigid_model does not carry
    # one; None means "write no footer".
    bounding_box: Optional[tuple] = None
    # Bytes after the objects (and the bounding box, if any) that this
    # container itself does not describe. Empty for both .rigid_model
    # forms; for .rigid_model_animation it is the embedded headerless
    # .anim that drives the objects - see load(allow_trailing=True).
    trailing: bytes = b""

    @property
    def version(self) -> int:
        """The version its objects carry (they always agree in practice)."""
        return self.meshes[0].version if self.meshes else 5

    def computed_bounding_box(self) -> tuple:
        """The bounds the footer should hold, from the geometry."""
        boxes = [m.positions for m in self.meshes if len(m.positions)]
        if not boxes:
            return (0.0,) * 6
        allpos = np.concatenate(boxes)
        return tuple(allpos.min(0).tolist()) + tuple(allpos.max(0).tolist())


# ---------------------------------------------------------------------------
# Reader plumbing
# ---------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.off = 0

    def _need(self, size: int):
        if size < 0 or self.off + size > len(self.data):
            raise ArmFormatError(
                f"Unexpected end of file at offset {self.off} "
                f"(need {size} more bytes, file is {len(self.data)})")

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        self._need(size)
        values = struct.unpack_from(fmt, self.data, self.off)
        self.off += size
        return values

    def u32(self) -> int:
        return self.unpack("<I")[0]

    def u8(self) -> int:
        return self.unpack("<B")[0]

    def string(self) -> str:
        """u16 *character* count followed by UTF-16LE."""
        length = self.unpack("<H")[0] * 2
        self._need(length)
        raw = self.data[self.off:self.off + length]
        self.off += length
        return raw.decode("utf-16-le", errors="replace")

    def floats(self, count: int) -> np.ndarray:
        size = count * 4
        self._need(size)
        out = np.frombuffer(self.data, "<f4", count=count, offset=self.off)
        self.off += size
        return out

    @property
    def remaining(self) -> int:
        return len(self.data) - self.off


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

# How to decide whether an object ends with a bone index.
BONE_LOOKAHEAD = "lookahead"    # normal files: peek for the next magic
BONE_ALWAYS = "always"          # headerless, animatable form
BONE_NEVER = "never"            # headerless, plain form


def load(data: bytes, allow_trailing: bool = False) -> ArmFile:
    """Parse an .animatable_rigid_model file from bytes.

    With `allow_trailing`, anything left after the objects is kept on
    `ArmFile.trailing` instead of raising.  That is what makes
    .rigid_model_animation readable: it is this exact container followed
    by an embedded headerless .anim, and only the caller knows to expect
    one (see import_rma).
    """
    if not _is_headerless(data):
        return _load(data, allow_trailing, BONE_LOOKAHEAD)

    # A headerless file has no magic to look ahead for, so whether its
    # objects carry a bone index cannot be decided one object at a time.
    # It is a property of the whole file, and the wrong choice derails
    # almost immediately, so try both and keep the reading that consumes
    # the file exactly.  Vanilla has examples of each.
    first = None
    for mode in (BONE_ALWAYS, BONE_NEVER):
        try:
            return _load(data, allow_trailing, mode)
        except ArmFormatError as exc:
            if first is None:
                first = exc
    raise first


def _is_headerless(data: bytes) -> bool:
    """Whether the objects drop their magic and version words.

    A real object opens with the magic; if the first one does not, this
    is the headerless variant, where the object count is followed
    straight by the first name.  A file with no objects at all says
    nothing either way, so it is read as an ordinary one.
    """
    if len(data) < 8:
        return False
    count, = struct.unpack_from("<I", data, 0)
    return bool(count) and struct.unpack_from("<I", data, 4)[0] != ARM_MAGIC


def make_reader(data: bytes, off: int = 0) -> "_Reader":
    """A reader positioned into `data`, for callers outside this module.

    vwm_format embeds whole objects and needs to hand read_object_body
    the kind of reader it expects, then pick the offset back up.
    """
    r = _Reader(data)
    r.off = off
    return r


def read_object_body(r: "_Reader", mesh: ArmMesh, index: int = 0,
                     texture_block: bool = True):
    """Read one object's textures, parameters and geometry into `mesh`.

    Everything between the version word and the optional trailing bone
    index.  Split out because .variant_weighted_mesh embeds objects of
    exactly this shape (with a name and bone index of its own in front)
    for the props a unit carries - see vwm_format.  Its oldest
    attachments have no texture slots at all, which is what
    texture_block=False is for.
    """
    if texture_block:
        slot_count, flagged = texture_layout(mesh.version)
        for slot in range(slot_count):
            if slot < flagged:
                mesh.texture_flags[slot] = r.u8()
            mesh.textures[slot] = r.string()

    # The named material parameter blocks only exist from version 4;
    # versions 1 and 3 go straight from the textures to the geometry.
    if mesh.version >= _PARAMS_FROM_VERSION:
        param_count = r.u32()
        if param_count > 10000:
            raise ArmFormatError(
                f"Object {index}: implausible float parameter count "
                f"{param_count}")
        for _ in range(param_count):
            name = r.string()
            mesh.float_params.append((name, r.unpack("<f")[0]))

        vec4_count = r.u32()
        if vec4_count > 10000:
            raise ArmFormatError(
                f"Object {index}: implausible vec4 parameter count "
                f"{vec4_count}")
        for _ in range(vec4_count):
            name = r.string()
            mesh.vec4_params.append((name, tuple(r.unpack("<4f"))))

    vertex_count = r.u32()
    if vertex_count > 10000000:
        raise ArmFormatError(
            f"Object {index}: implausible vertex count {vertex_count}")
    stride = vertex_floats(mesh.version)
    block = r.floats(vertex_count * stride)
    block = block.reshape(vertex_count, stride)
    mesh.positions = block[:, _POS].astype(np.float32)
    mesh.normals = block[:, _NORMAL].astype(np.float32)
    mesh.uv0 = block[:, _UV].astype(np.float32)
    mesh.tangents = block[:, _TANGENT].astype(np.float32)
    mesh.binormals = block[:, _BINORMAL].astype(np.float32)
    # The older strides drop channels off the end - version 1 has no
    # second UV set, the headerless form has no colour either.  Give
    # them all-zero ones so every ArmMesh has the same shape whatever
    # it was read from.
    mesh.colours = (block[:, _COLOUR].astype(np.float32)
                    if stride >= V1_VERTEX_FLOATS
                    else np.zeros((vertex_count, 4), np.float32))
    mesh.uv1 = (block[:, _UV2].astype(np.float32)
                if stride == VERTEX_FLOATS
                else np.zeros((vertex_count, 2), np.float32))

    index_count = r.u32()
    if index_count > 30000000:
        raise ArmFormatError(
            f"Object {index}: implausible index count {index_count}")
    size = index_count * 4
    r._need(size)
    mesh.indices = np.frombuffer(
        r.data, "<u4", count=index_count, offset=r.off).copy()
    r.off += size


def _load(data: bytes, allow_trailing: bool, bone_mode: str) -> ArmFile:
    r = _Reader(data)
    arm = ArmFile()

    count = r.u32()
    if count > 100000:
        raise ArmFormatError(f"Implausible object count {count}")

    headerless = bone_mode != BONE_LOOKAHEAD

    for i in range(count):
        if headerless:
            mesh = ArmMesh(version=NO_HEADER_VERSION)
        else:
            magic = r.u32()
            if magic != ARM_MAGIC:
                raise ArmFormatError(
                    f"Object {i}: bad magic 0x{magic:08x} (expected "
                    f"0x{ARM_MAGIC:08x}) - is this really an "
                    f".animatable_rigid_model file?")
            mesh = ArmMesh(version=r.u32())
            if mesh.version not in SUPPORTED_VERSIONS:
                raise ArmFormatError(
                    f"Object {i}: unsupported version {mesh.version} "
                    f"(supported: {SUPPORTED_VERSIONS})")

        read_object_body(r, mesh, i)

        # .animatable_rigid_model ends each object with a bone index;
        # plain .rigid_model does not, and instead closes the whole file
        # with a bounding box. Both are optional and the file says which
        # only by what is left, so decide by looking ahead - except in a
        # headerless file, where there is no magic to look ahead for and
        # the caller has already picked a mode for the whole file.
        if bone_mode == BONE_NEVER:
            mesh.bone_index = None
        elif bone_mode == BONE_ALWAYS:
            mesh.bone_index = r.u32()
        elif (r.remaining >= 4
                and struct.unpack_from("<I", r.data, r.off)[0] == ARM_MAGIC):
            mesh.bone_index = None          # next object starts right here
        elif r.remaining in (0, _BBOX_SIZE):
            mesh.bone_index = None          # last object: footer, or end
        else:
            mesh.bone_index = r.u32()
        arm.meshes.append(mesh)

    if r.remaining == _BBOX_SIZE:
        arm.bounding_box = tuple(r.unpack("<6f"))

    if allow_trailing:
        arm.trailing = r.data[r.off:]
        return arm

    if r.remaining:
        raise ArmFormatError(
            f"{r.remaining} unparsed bytes at the end of the file")
    return arm


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def _write_string(out: bytearray, text: str):
    raw = text.encode("utf-16-le", errors="replace")
    if len(raw) // 2 > 0xFFFF:
        raise ArmFormatError(f"String too long to store: {text[:40]}...")
    out += struct.pack("<H", len(raw) // 2)
    out += raw


def write_object_body(out: bytearray, mesh: ArmMesh, index: int = 0,
                      texture_block: bool = True):
    """Serialize one object's textures, parameters and geometry.

    The inverse of read_object_body, and shared with vwm_format for the
    same reason.
    """
    if texture_block:
        slot_count, flagged = texture_layout(mesh.version)
        extra = [name for name in mesh.textures[slot_count:] if name]
        if extra:
            raise ArmFormatError(
                f"Object {index}: version {mesh.version} has only "
                f"{slot_count} texture slot(s) "
                f"({', '.join(TEXTURE_SLOTS[:slot_count])}), but this "
                f"mesh also carries {', '.join(extra)}")
        for slot in range(slot_count):
            if slot < flagged:
                out += bytes((mesh.texture_flags[slot] & 0xFF,))
            _write_string(out, mesh.textures[slot])

    if mesh.version >= _PARAMS_FROM_VERSION:
        out += struct.pack("<I", len(mesh.float_params))
        for name, value in mesh.float_params:
            _write_string(out, name)
            out += struct.pack("<f", value)

        out += struct.pack("<I", len(mesh.vec4_params))
        for name, value in mesh.vec4_params:
            _write_string(out, name)
            out += struct.pack("<4f", *value)
    elif mesh.float_params or mesh.vec4_params:
        raise ArmFormatError(
            f"Object {index}: version {mesh.version} has no material "
            "parameter block to write these into")

    n = mesh.vertex_count
    stride = vertex_floats(mesh.version)
    channels = [("normals", mesh.normals, 3), ("uv0", mesh.uv0, 2),
                ("tangents", mesh.tangents, 3),
                ("binormals", mesh.binormals, 3)]
    if stride >= V1_VERTEX_FLOATS:
        channels.append(("colours", mesh.colours, 4))
    if stride == VERTEX_FLOATS:
        channels.append(("uv1", mesh.uv1, 2))
    for label, array, width in channels:
        if len(array) != n:
            raise ArmFormatError(
                f"Object {index}: {label} has {len(array)} entries but "
                f"there are {n} vertices")
        if array.shape[1] != width:
            raise ArmFormatError(
                f"Object {index}: {label} must have {width} components")

    block = np.zeros((n, stride), np.float32)
    block[:, _POS] = mesh.positions
    block[:, _NORMAL] = mesh.normals
    block[:, _UV] = mesh.uv0
    block[:, _TANGENT] = mesh.tangents
    block[:, _BINORMAL] = mesh.binormals
    if stride >= V1_VERTEX_FLOATS:
        block[:, _COLOUR] = mesh.colours
    if stride == VERTEX_FLOATS:
        block[:, _UV2] = mesh.uv1
    out += struct.pack("<I", n)
    out += block.astype("<f4").tobytes()

    indices = np.asarray(mesh.indices, np.uint32)
    if n and len(indices) and indices.max() >= n:
        raise ArmFormatError(
            f"Object {index}: index {indices.max()} is out of range for "
            f"{n} vertices")
    out += struct.pack("<I", len(indices))
    out += indices.astype("<u4").tobytes()


def save(arm: ArmFile) -> bytes:
    """Serialize an ArmFile to bytes."""
    out = bytearray()
    out += struct.pack("<I", len(arm.meshes))

    headerless = bool(arm.meshes) and         arm.meshes[0].version == NO_HEADER_VERSION
    # A headerless file has no magic to separate its objects, so the
    # reader decides once per file whether they all carry a bone index.
    # A file that mixed the two could not be read back at all.
    if headerless and len({mesh.bone_index is None
                           for mesh in arm.meshes}) > 1:
        raise ArmFormatError(
            "A headerless file's objects either all carry a bone index "
            "or none do, but this one mixes the two")
    for i, mesh in enumerate(arm.meshes):
        if headerless != (mesh.version == NO_HEADER_VERSION):
            raise ArmFormatError(
                f"Object {i}: a file is either headerless throughout or "
                "not at all, but this one mixes the two")
        if not headerless:
            if mesh.version not in SUPPORTED_VERSIONS:
                raise ArmFormatError(
                    f"Object {i}: cannot write version {mesh.version} "
                    f"(writable: {SUPPORTED_VERSIONS})")
            out += struct.pack("<II", ARM_MAGIC, mesh.version)

        write_object_body(out, mesh, i)

        # No bone index means this is a plain .rigid_model object.
        if mesh.bone_index is not None:
            out += struct.pack("<I", mesh.bone_index)


    if arm.bounding_box is not None:
        out += struct.pack("<6f", *arm.bounding_box)
    out += arm.trailing

    return bytes(out)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

# The parameter block CA writes for a plain lit/bumped object.  Used when
# building a file from scratch so the result matches vanilla conventions.
DEFAULT_FLOAT_PARAMS = [
    ("light_scale", 1.0),
    ("offsetu0", 0.0),
    ("offsetv0", 0.0),
    ("bumpfactor", 1.0),
    ("specpower", 0.5),
    ("specbrightness", 0.5),
    ("specularfresnelpower", 0.01),
    ("glossfactor", 2.0),
    ("fresnelpower", 0.1),
    ("reflect_factor", 0.5),
    ("ambientfactor", 0.6),
]

DEFAULT_VEC4_PARAMS = [
    ("colourmapfactor", (1.0, 1.0, 1.0, 0.0)),
    ("specfactor", (1.0, 1.0, 1.0, 1.0)),
]


def default_params() -> tuple:
    """Fresh copies of the vanilla default parameter blocks."""
    return (list(DEFAULT_FLOAT_PARAMS), list(DEFAULT_VEC4_PARAMS))
