"""Binary reader/writer for Creative Assembly's .anim skeleton/animation
format.

Like rmv2_format, this module is intentionally free of any Blender (bpy)
dependencies so it can be unit-tested with a plain Python interpreter.

The implementation follows the C# reference in
TheAssetEditor/Shared/GameFiles/Animation/AnimationFile.cs:

    header      version (u32), a type word (u32, see header_type),
                frame rate (f32),
                skeleton name (u16-length + utf8),
                v7+: flag count (u32) + flag strings,
                total playtime in seconds (f32)
    bones       count (u32), then per bone: name (u16-length + utf8),
                parent index (i32, -1 = root)
    v8 only     one extra u32 after the bone table
    per part    translation mappings (bone_count i32),
                rotation mappings (bone_count i32),
                v7: static frame (pos count u32, rot count u32, frame),
                dynamic pos count (i32), rot count (i32), frame count (i32),
                then the frames

A frame is `pos_count` float32 vec3 translations followed by `rot_count`
xyzw quaternions stored as 4 int16 (value / 32767).  A bone mapping value
of -1 means "no data, use the skeleton's bind pose", 0..9999 indexes into
each dynamic frame and >= 10000 indexes into the static frame (minus 10000).
Versions 5 and 6 have exactly one part and no static frame; version 8
splits the data into parts, each packing its channels at a per-bone rate
(see AnimPart's rate fields).  AssetEditor reads v8 but will not write
it; this module does both.

Version 4 is Rome 2's original, dropped during that game's own run and
described nowhere this project knows of.  It has the header above with
its strings as UTF-16 (a *character* count, the way Shogun 2 writes
them), and then, in place of the mapping tables:

    flags       translation: bone count (u32) + that many bits packed
                into u32 words, low bit first; then the same again for
                rotation
    frames      count (u32), then count * bone_count * 28 bytes: a
                float32 vec3 translation followed by an xyzw float32
                quaternion, for every bone, in bone order

So v4 stores every bone in every frame and quantizes nothing, exactly
like Shogun 2's v1 - the compression that v5 introduces is the whole
point of the version bump.  What the two bitfields mean is not settled;
see AnimPart.translation_flags.

Rome 2 also closes some version 5 files with the event block Shogun 2
files carry (see AnimFile.has_event_block).  All 59 of them are empty.

Skeleton files (animations/skeletons/*.anim) are ordinary .anim files
carrying a few identical copies of the bind pose as dynamic frames -
they are structurally indistinguishable from short animations.

Version 1 (Shogun 2) predates all of that and has its own, much simpler
layout.  It is not described by AssetEditor - it was reverse-engineered
from real files (see tests/data) and cross-checked against the user's
standalone Shogun 2 scripts:

    header      version (u32, always 1), frame rate (f32),
                total playtime in seconds (f32)
    bones       count (u32), then per bone: name (u16 *character* count +
                UTF-16LE), parent index (i32, -1 = root)
    frames      count (u32), then count * bone_count * 28 bytes:
                a float32 vec3 translation followed by an xyzw
                float32 quaternion, for every bone, in bone order
    events      count (u32), then per event: string count (u32) followed
                by that many UTF-16LE strings

Every bone is animated in every frame, so v1 has no mapping tables, no
static frame and no parts; it is loaded into the same data model with a
single part whose mappings are all dynamic.  Note that v1 stores strings
as UTF-16 with a *character* count, where v5+ uses UTF-8 with a *byte*
count, and stores quaternions as full float32 rather than int16.

A minority of Shogun 2 files (campaign pieces, a few reference skeletons)
use a second, headerless variant with no version field at all: they begin
at the frame rate, and each bone carries ten floats per frame rather than
seven - the extra three are 0.001 in every file seen and their purpose is
unknown.  They are given the synthetic version number 0, which is never
written to disk; load() recognises them by checking whether the first four
bytes make a plausible frame rate when no known version number is found.

Both Shogun 2 layouts end in one event block (animation markers such as
FIRE_TIME); CA's cinematic test exports append a second, empty one.

Coordinates are in the game's space (right-handed, Y-up); conversion to
Blender space happens elsewhere.  All data is little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

# Version 1 is Shogun 2 (see the module docstring); it shares none of the
# v5+ body layout and is handled by its own reader/writer below.
#
# Version 4 is Rome 2's first version, decoded from its own files: like v1
# it uses UTF-16 strings and stores every bone in every frame, but the rest
# of the body is v5's.  AssetEditor cannot read real v4 files.
SHOGUN2_VERSION = 1

# Some Shogun 2 files (campaign pieces, and a few reference skeletons) have
# no version field at all and start straight at the frame rate, with ten
# floats per bone per frame instead of seven.  There is no number in the
# file to name them by, so version 0 is used as a synthetic id; it is never
# written into a file.  See _load_v0.
SHOGUN2_NO_HEADER_VERSION = 0

# Version numbers that actually appear in a file's first field.  The
# headerless variant has none, so 0 is absent here but writable.
SUPPORTED_READ_VERSIONS = (1, 4, 5, 6, 7, 8)
SUPPORTED_WRITE_VERSIONS = (0, 1, 4, 5, 6, 7, 8)

# Rome 2 shipped with version 4, then switched to 5 during its own run.
# The two are the same layout; 4 stores its strings the way Shogun 2 does
# (a UTF-16 character count), 5 the way everything after it does (a UTF-8
# byte count).  The .rigid_model_v2 versions either side of that switch,
# 5 and 6, differ in exactly the same way.
UTF16_STRING_VERSION = 4

# Frame rates seen in real files are 20 and 30; this bound only has to
# separate a plausible rate from a version number or garbage.
_PLAUSIBLE_FRAME_RATE = (1.0, 1000.0)

MAPPING_NONE = -1
_STATIC_BASE = 10000


class AnimFormatError(Exception):
    pass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AnimBone:
    name: str
    parent: int = -1        # index into the bone list, -1 = root


class BoneMapping:
    """One translation/rotation mapping value as stored in the file."""

    def __init__(self, value: int):
        self.value = int(value)

    @property
    def is_none(self) -> bool:
        return self.value == MAPPING_NONE

    @property
    def is_static(self) -> bool:
        return self.value >= _STATIC_BASE

    @property
    def is_dynamic(self) -> bool:
        return not self.is_none and not self.is_static

    @property
    def index(self) -> int:
        return self.value - _STATIC_BASE if self.is_static else self.value

    def __repr__(self):
        if self.is_none:
            return "BoneMapping(none)"
        kind = "static" if self.is_static else "dynamic"
        return f"BoneMapping({kind} {self.index})"


@dataclass
class AnimFrame:
    """One frame: (n,3) translations and (n,4) xyzw quats (already
    divided by 32767).

    float32, except in version 8, where the frames are float64.  Its
    byte-packed channels decode as `base + (byte / 127) * scale`, and
    where the base dwarfs the scale - Warhammer 3 has bones at a ratio
    of six million - a single byte step is finer than float32 resolves.
    Rounding the decode to float32 would quietly merge adjacent bytes,
    and the file could no longer be written back as it was read."""
    translations: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    rotations: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 4), np.float32))
    # Version 0 only: three further floats per bone, 0.001 in every file
    # seen.  Purpose unknown (they are not scale - that would be 1.0); kept
    # verbatim so those files re-save unchanged.
    extras: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))


@dataclass
class AnimPart:
    translation_mappings: list = field(default_factory=list)  # [BoneMapping]
    rotation_mappings: list = field(default_factory=list)     # [BoneMapping]
    static_frame: AnimFrame | None = None
    dynamic_frames: list = field(default_factory=list)        # [AnimFrame]
    # The frame-count word when a part has no dynamic frames at all - a
    # pose held entirely in the static frame.  It is usually 3, which is
    # why it read as a quirk, but in CA's files it is really
    # round(duration * frame_rate) + 1: the length the animation would
    # have had, written even though no frames follow.  81 for a 4-second
    # pose at 20 fps, 7 for a 0.3-second one.  None means "write 3", so
    # a file built from scratch behaves as before; loading keeps the
    # real value, which is what makes those files re-save unchanged.
    declared_frame_count: int | None = None

    # Version 8 only.  A v8 part does not store its channels plainly: a
    # per-bone int8 "rate" says how each one is packed, and the byte
    # encodings decode through a per-bone range.  The rate's sign is
    # also the mapping table (negative static, positive dynamic, zero
    # not animated), so the mappings above are derived from these on
    # load rather than stored separately in the file.
    #
    #     translations: 12 = three float32, 3 = three int8 through a range
    #     rotations:     8 = four int16/32767, 4 = four int8 through a range
    #
    # None means "not read from a file": the writer then picks the
    # uncompressed rates (12 and 8), which need no ranges and lose
    # nothing.  Ranges are kept exactly as read because their array
    # length does not follow from the rates - CA writes a count of its
    # own choosing, longer than the last ranged bone in about a third of
    # parts.
    translation_rates: np.ndarray | None = None       # int8, per bone
    rotation_rates: np.ndarray | None = None          # int8, per bone
    translation_ranges: np.ndarray | None = None      # (n, 2, 3) f32
    rotation_ranges: np.ndarray | None = None         # (n, 2, 4) f32

    # Version 4 only: a per-bone bit for each channel, sitting where v5
    # keeps its mapping tables.  They are not mappings - a v4 file stores
    # every bone in every frame whatever the bits say.  In 24 of Rome 2's
    # 31 files the set bits are exactly the bones that move, and in the
    # other 7 a few bones move with their bit clear, so they read as the
    # channels the animator meant to drive rather than anything the
    # decoder can act on.  None means "all set", which is what a file
    # built from scratch gets.
    translation_flags: np.ndarray | None = None       # (bones,) bool
    rotation_flags: np.ndarray | None = None


@dataclass
class AnimFile:
    version: int = 7
    frame_rate: float = 20.0
    skeleton_name: str = ""
    flags: list = field(default_factory=list)       # v7+ flag strings
    duration: float = 0.0                           # total playtime, seconds
    # The u32 straight after the version.  AssetEditor treats it as a
    # constant 1 and so did this reader, but it is not: of Warhammer 3's
    # 34921 animations, 38 (all v7, all bird03 attacks) carry 2 and one
    # (a v8 bigcat03b idle) carries 0, and Three Kingdoms uses all three
    # freely.  Warhammer 1 is the file set that gives it a shape: 291 of
    # its 6975 are 0, and nearly all of those are the animations that
    # ride on a rigid model - buildings, chariots, war machines - rather
    # than a character rig.  Whatever it means, it has to be kept, or
    # those files do not re-save.
    header_type: int = 1
    # Version 8 only: the u32 after the bone table.  6 in every
    # vanilla file, so that is what a new one gets.
    unknown_v8: int = 6
    bones: list = field(default_factory=list)       # [AnimBone]
    parts: list = field(default_factory=list)       # [AnimPart]
    # Shogun 2 trailing metadata: a list of string tuples, e.g.
    # ("FIRE_TIME", "0.46") or ("FIRE_POSITION", "0.00", "3.66", "0.24").
    events: list = field(default_factory=list)
    # Shogun 2 always writes the block above; Rome 2 only writes it for
    # some version 5 files, so whether it is there has to be remembered.
    has_event_block: bool = False
    # CA's cinematic test exports carry a second, always-empty event block
    # after the first.  Each entry here is another list of string tuples.
    extra_event_blocks: list = field(default_factory=list)

    @property
    def bone_count(self) -> int:
        return len(self.bones)

    @property
    def frame_count(self) -> int:
        return sum(len(p.dynamic_frames) for p in self.parts)


@dataclass
class ResolvedAnim:
    """Per-bone tracks with the mapping indirection applied.

    translations: (frames, bones, 3) float32, rotations: (frames, bones, 4)
    float32 xyzw.  Bones whose mapping is `none` carry identity values and
    are flagged False in has_translation/has_rotation - the consumer should
    substitute the skeleton's bind pose for those.
    """
    translations: np.ndarray
    rotations: np.ndarray
    has_translation: np.ndarray     # (bones,) bool
    has_rotation: np.ndarray        # (bones,) bool
    static_translation: np.ndarray  # (bones,) bool: constant over time
    static_rotation: np.ndarray


# ---------------------------------------------------------------------------
# Reader plumbing
# ---------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.off = 0

    def _need(self, size: int):
        if self.off + size > len(self.data):
            raise AnimFormatError(
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

    def i32(self) -> int:
        return self.unpack("<i")[0]

    def f32(self) -> float:
        return self.unpack("<f")[0]

    def string(self) -> str:
        length = self.unpack("<H")[0]
        self._need(length)
        raw = self.data[self.off:self.off + length]
        self.off += length
        return raw.decode("utf-8", errors="replace")

    def string_utf16(self) -> str:
        """v1 strings: a *character* count followed by UTF-16LE data."""
        length = self.unpack("<H")[0] * 2
        self._need(length)
        raw = self.data[self.off:self.off + length]
        self.off += length
        return raw.decode("utf-16-le", errors="replace")

    def bytes_(self, n: int) -> bytes:
        self._need(n)
        raw = self.data[self.off:self.off + n]
        self.off += n
        return raw

    @property
    def remaining(self) -> int:
        return len(self.data) - self.off


_QUAT_SCALE = 1.0 / 32767.0


def _read_frame(r: _Reader, pos_count: int, rot_count: int,
                quat_float: bool = False) -> AnimFrame:
    """Read one frame.  v5+ packs quaternions as int16/32767; v1 (Shogun 2)
    stores them as plain float32 - hence `quat_float`."""
    frame = AnimFrame()
    if pos_count:
        raw = np.frombuffer(r.bytes_(pos_count * 12), dtype="<f4")
        frame.translations = raw.reshape(pos_count, 3).astype(np.float32)
    if rot_count:
        if quat_float:
            raw = np.frombuffer(r.bytes_(rot_count * 16), dtype="<f4")
            frame.rotations = raw.reshape(rot_count, 4).astype(np.float32)
        else:
            raw = np.frombuffer(r.bytes_(rot_count * 8), dtype="<i2")
            frame.rotations = (raw.reshape(rot_count, 4).astype(np.float32)
                               * _QUAT_SCALE)
    return frame


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(data: bytes) -> AnimFile:
    r = _Reader(data)
    anim = AnimFile()

    anim.version = r.u32()
    if anim.version not in SUPPORTED_READ_VERSIONS:
        # No version field at all?  Then those 4 bytes are the frame rate.
        rate = struct.unpack_from("<f", data, 0)[0]
        if _PLAUSIBLE_FRAME_RATE[0] <= rate <= _PLAUSIBLE_FRAME_RATE[1]:
            r.off = 0
            anim.version = SHOGUN2_NO_HEADER_VERSION
            return _load_v0(r, anim)
        raise AnimFormatError(
            f"Unsupported .anim version {anim.version} - is this really "
            f"a .anim file? (supported: {SUPPORTED_READ_VERSIONS})")
    if anim.version == SHOGUN2_VERSION:
        return _load_v1(r, anim)
    read_string = (r.string_utf16 if anim.version == UTF16_STRING_VERSION
                   else r.string)
    anim.header_type = r.u32()
    anim.frame_rate = r.f32()
    anim.skeleton_name = read_string()

    if anim.version > 6:
        flag_count = r.u32()
        if flag_count > 1000:
            raise AnimFormatError(f"Implausible flag count {flag_count}")
        anim.flags = [read_string() for _ in range(flag_count)]

    anim.duration = r.f32()

    bone_count = r.u32()
    if bone_count > 100000:
        raise AnimFormatError(f"Implausible bone count {bone_count}")
    for i in range(bone_count):
        name = read_string()
        parent = r.i32()
        anim.bones.append(AnimBone(name=name, parent=parent))

    if anim.version == 8:
        anim.unknown_v8 = r.u32()
        anim.parts = _load_parts_v8(r, bone_count)
    else:
        if anim.version == UTF16_STRING_VERSION:
            anim.parts = [_load_part_v4(r, bone_count)]
        else:
            anim.parts = [_load_part_default(r, bone_count, anim.version)]
        # Rome 2's cutscene animations close with the same event block
        # Shogun 2 files carry.  All 59 vanilla ones are empty, so the
        # count is all that has ever been seen - but a populated block
        # reads with the Shogun 2 parser, and is written back the same
        # way.
        if r.remaining:
            anim.has_event_block = True
            anim.events = _read_event_block(r)

    if r.remaining:
        raise AnimFormatError(
            f"{r.remaining} unparsed bytes at the end of the file")
    return anim


def _read_event_block(r: _Reader) -> list:
    event_count = r.u32()
    if event_count > 100000:
        raise AnimFormatError(f"Implausible event count {event_count}")
    events = []
    for _ in range(event_count):
        string_count = r.u32()
        if string_count > 1000:
            raise AnimFormatError(
                f"Implausible event string count {string_count}")
        events.append(tuple(r.string_utf16() for _ in range(string_count)))
    return events


def _read_interleaved_frames(r: _Reader, part: AnimPart, bone_count: int,
                             frame_count: int, floats_per_bone: int):
    """Read frames stored per bone rather than as a translation block and a
    rotation block: bone_count * (vec3 + xyzw quat [+ extras]), all
    float32.  Shogun 2 and Rome 2's version 4 both store frames this way.
    """
    if bone_count:
        need = frame_count * bone_count * floats_per_bone * 4
        raw = np.frombuffer(r.bytes_(need), dtype="<f4")
        raw = raw.reshape(frame_count, bone_count, floats_per_bone)
        for f in range(frame_count):
            frame = AnimFrame(
                translations=raw[f, :, 0:3].astype(np.float32),
                rotations=raw[f, :, 3:7].astype(np.float32))
            if floats_per_bone > 7:
                frame.extras = raw[f, :, 7:floats_per_bone].astype(np.float32)
            part.dynamic_frames.append(frame)
    else:
        # No bones means no frame data, but the count still has to survive
        # a load/save cycle (CA ships a few such stubs).
        part.dynamic_frames.extend(AnimFrame() for _ in range(frame_count))


def _write_interleaved_frames(out: bytearray, part: AnimPart,
                              bone_count: int, floats_per_bone: int,
                              extras_default: float = 0.0):
    """The counterpart of _read_interleaved_frames."""
    for frame in part.dynamic_frames:
        if not bone_count:
            continue        # a bone-less stub carries no frame data
        if (len(frame.translations) != bone_count
                or len(frame.rotations) != bone_count):
            raise AnimFormatError(
                f"This layout needs one translation and one rotation per "
                f"bone per frame (expected {bone_count}, got "
                f"{len(frame.translations)}/{len(frame.rotations)})")
        # Interleaved per bone: vec3 translation then xyzw quaternion.
        block = np.empty((bone_count, floats_per_bone), np.float32)
        block[:, 0:3] = frame.translations
        block[:, 3:7] = frame.rotations
        if floats_per_bone > 7:
            if len(frame.extras) == bone_count:
                block[:, 7:floats_per_bone] = frame.extras
            elif len(frame.extras) == 0:
                block[:, 7:floats_per_bone] = extras_default
            else:
                raise AnimFormatError(
                    f"Version 0 needs three extra floats per bone per "
                    f"frame (expected {bone_count}, got "
                    f"{len(frame.extras)})")
        out += block.astype("<f4").tobytes()


def _require_all_dynamic(part: AnimPart, label: str):
    """Layouts that store every bone in every frame have no way to express
    a static or unmapped bone."""
    for name, mappings in (("translation", part.translation_mappings),
                           ("rotation", part.rotation_mappings)):
        for i, mapping in enumerate(mappings):
            if not mapping.is_dynamic or mapping.index != i:
                raise AnimFormatError(
                    f"{label} files require every bone to be dynamic: "
                    f"bone {i} has {name} mapping {mapping}")
    if part.static_frame is not None:
        raise AnimFormatError(f"{label} files have no static frame")


def _read_v4_flags(r: _Reader, bone_count: int) -> np.ndarray:
    """A count, then that many bits packed into u32 words, low bit first."""
    count = r.u32()
    if count != bone_count:
        raise AnimFormatError(
            f"Version 4 flag count {count} does not match the "
            f"{bone_count} bones in the file")
    words = r.unpack("<%dI" % ((count + 31) // 32)) if count else ()
    bits = np.zeros(count, bool)
    for i in range(count):
        bits[i] = (words[i // 32] >> (i % 32)) & 1
    return bits


def _write_v4_flags(out: bytearray, flags, bone_count: int):
    out += struct.pack("<I", bone_count)
    words = [0] * ((bone_count + 31) // 32)
    for i in range(bone_count):
        if flags is None or flags[i]:
            words[i // 32] |= 1 << (i % 32)
    for word in words:
        out += struct.pack("<I", word)


def _load_part_v4(r: _Reader, bone_count: int) -> AnimPart:
    """Rome 2's original layout: two per-bone bitfields, then every bone's
    translation and rotation in every frame, as float32."""
    part = AnimPart()
    part.translation_mappings = [BoneMapping(i) for i in range(bone_count)]
    part.rotation_mappings = [BoneMapping(i) for i in range(bone_count)]
    part.translation_flags = _read_v4_flags(r, bone_count)
    part.rotation_flags = _read_v4_flags(r, bone_count)

    frame_count = r.u32()
    if frame_count > 1000000:
        raise AnimFormatError(f"Implausible frame count {frame_count}")
    _read_interleaved_frames(r, part, bone_count, frame_count, 7)
    return part


def _load_shogun2_body(r: _Reader, anim: AnimFile,
                       floats_per_bone: int) -> AnimFile:
    """Shared body of the two Shogun 2 layouts; `r` is positioned at the
    frame rate.  floats_per_bone is 7 for v1 and 10 for v0."""
    anim.frame_rate = r.f32()
    anim.duration = r.f32()

    bone_count = r.u32()
    if bone_count > 100000:
        raise AnimFormatError(f"Implausible bone count {bone_count}")
    for _ in range(bone_count):
        name = r.string_utf16()
        anim.bones.append(AnimBone(name=name, parent=r.i32()))

    frame_count = r.u32()
    if frame_count > 1000000:
        raise AnimFormatError(f"Implausible frame count {frame_count}")

    # Every bone is animated in every frame, so the mapping tables that
    # v5+ files carry are implicit here: bone i is dynamic entry i.
    part = AnimPart()
    part.translation_mappings = [BoneMapping(i) for i in range(bone_count)]
    part.rotation_mappings = [BoneMapping(i) for i in range(bone_count)]

    _read_interleaved_frames(r, part, bone_count, frame_count,
                             floats_per_bone)
    anim.parts = [part]

    anim.events = _read_event_block(r)
    anim.has_event_block = True
    # A second, always-empty event block appears in CA's cinematic test
    # exports.  Read any that are present so they can be written back.
    while r.remaining >= 4:
        anim.extra_event_blocks.append(_read_event_block(r))

    if r.remaining:
        raise AnimFormatError(
            f"{r.remaining} unparsed bytes at the end of the file")
    return anim


def _load_v1(r: _Reader, anim: AnimFile) -> AnimFile:
    """Read a Shogun 2 (version 1) file; `r` is positioned after the
    version field.  See the module docstring for the layout."""
    return _load_shogun2_body(r, anim, floats_per_bone=7)


def _load_v0(r: _Reader, anim: AnimFile) -> AnimFile:
    """Read the headerless Shogun 2 variant; `r` is positioned at byte 0
    (there is no version field to skip)."""
    return _load_shogun2_body(r, anim, floats_per_bone=10)


def _load_part_default(r: _Reader, bone_count: int,
                       version: int) -> AnimPart:
    part = AnimPart()
    part.translation_mappings = [BoneMapping(r.i32())
                                 for _ in range(bone_count)]
    part.rotation_mappings = [BoneMapping(r.i32())
                              for _ in range(bone_count)]

    # A single constant frame (hand poses etc.); v7 only.
    if version == 7:
        static_pos = r.u32()
        static_rot = r.u32()
        if static_pos or static_rot:
            part.static_frame = _read_frame(r, static_pos, static_rot)

    pos_count = r.i32()
    rot_count = r.i32()
    frame_count = r.i32()
    if not (pos_count or rot_count):
        # No dynamic data, so this word is a leftover - see
        # AnimPart.declared_frame_count.
        part.declared_frame_count = frame_count
    if pos_count or rot_count:
        if frame_count < 0 or frame_count > 1000000:
            raise AnimFormatError(f"Implausible frame count {frame_count}")
        for _ in range(frame_count):
            part.dynamic_frames.append(_read_frame(r, pos_count, rot_count))
    return part


def _load_parts_v8(r: _Reader, bone_count: int) -> list:
    parts = []
    part_count = r.u32()
    if part_count > 1000:
        raise AnimFormatError(f"Implausible part count {part_count}")

    for _ in range(part_count):
        parts.append(_load_part_v8(r, bone_count))
    return parts


def _load_part_v8(r: _Reader, bone_count: int) -> AnimPart:
    """One version 8 part: its per-bone rates, range maps and frames."""
    part = AnimPart()

    # Per-bone bit rates double as the mapping table: sign selects
    # static (negative) vs dynamic (positive) vs none (zero).
    trans_rates = np.frombuffer(r.bytes_(bone_count), dtype=np.int8)
    dyn = stat = 0
    for rate in trans_rates:
        if rate < 0:
            part.translation_mappings.append(
                BoneMapping(_STATIC_BASE + stat))
            stat += 1
        elif rate > 0:
            part.translation_mappings.append(BoneMapping(dyn))
            dyn += 1
        else:
            part.translation_mappings.append(BoneMapping(MAPPING_NONE))

    rot_rates = np.frombuffer(r.bytes_(bone_count), dtype=np.int8)
    dyn = stat = 0
    for rate in rot_rates:
        if rate < 0:
            part.rotation_mappings.append(
                BoneMapping(_STATIC_BASE + stat))
            stat += 1
        elif rate > 0:
            part.rotation_mappings.append(BoneMapping(dyn))
            dyn += 1
        else:
            part.rotation_mappings.append(BoneMapping(MAPPING_NONE))

    # Range maps for the byte-compressed encodings.  AssetEditor indexes
    # these by absolute bone index.
    trans_range_count = r.u32()
    rot_range_count = r.u32()
    trans_ranges = np.frombuffer(
        r.bytes_(trans_range_count * 24),
        dtype="<f4").reshape(trans_range_count, 2, 3)
    rot_ranges = np.frombuffer(
        r.bytes_(rot_range_count * 32),
        dtype="<f4").reshape(rot_range_count, 2, 4)
    # Keep the packing so the file can be written back as it was.
    part.translation_rates = trans_rates.copy()
    part.rotation_rates = rot_rates.copy()
    part.translation_ranges = trans_ranges.copy()
    part.rotation_ranges = rot_ranges.copy()

    static_pos = r.u32()
    static_rot = r.u32()
    if static_pos or static_rot:
        part.static_frame = _read_frame_v8(
            r, trans_rates, rot_rates, trans_ranges, rot_ranges,
            dynamic=False)

    dyn_pos = r.u32()
    dyn_rot = r.u32()
    frame_count = r.u32()
    if frame_count > 1000000:
        raise AnimFormatError(f"Implausible frame count {frame_count}")
    if not (dyn_pos or dyn_rot):
        # Same leftover as the v5-v7 layout - see
        # AnimPart.declared_frame_count.
        part.declared_frame_count = frame_count
    if dyn_pos or dyn_rot:
        for _ in range(frame_count):
            part.dynamic_frames.append(_read_frame_v8(
                r, trans_rates, rot_rates, trans_ranges, rot_ranges,
                dynamic=True))
    return part


def _read_frame_v8(r: _Reader, trans_rates, rot_rates, trans_ranges,
                   rot_ranges, dynamic: bool) -> AnimFrame:
    translations = []
    for bone, rate in enumerate(trans_rates):
        rate = int(rate) if dynamic else -int(rate)
        if rate == 12:
            translations.append(r.unpack("<3f"))
        elif rate == 3:
            # 3 signed bytes scaled into a per-bone range: max + n * min
            # (that really is the decode AssetEditor uses).
            if bone >= len(trans_ranges):
                raise AnimFormatError(
                    f"Bone {bone} uses a ranged translation but the range "
                    f"map only has {len(trans_ranges)} entries")
            # float64 throughout - see the note on AnimFrame.  A byte
            # step here can be finer than float32 resolves near a large
            # base, and rounding the decode now would lose which byte
            # this was.
            n = np.frombuffer(r.bytes_(3), np.int8).astype(np.float64) / 127.0
            lo, hi = trans_ranges[bone]
            translations.append(
                tuple(np.float64(hi) + n * np.float64(lo)))
        elif rate in (0, -12, -3):
            pass
        else:
            raise AnimFormatError(
                f"Unknown translation bit rate {rate} for bone {bone}")

    rotations = []
    for bone, rate in enumerate(rot_rates):
        rate = int(rate) if dynamic else -int(rate)
        if rate == 8:
            quat = np.array(r.unpack("<4h"), np.float32) * _QUAT_SCALE
            rotations.append(tuple(quat))
        elif rate == 4:
            if bone >= len(rot_ranges):
                raise AnimFormatError(
                    f"Bone {bone} uses a ranged rotation but the range "
                    f"map only has {len(rot_ranges)} entries")
            n = np.frombuffer(r.bytes_(4), np.int8).astype(np.float64) / 127.0
            lo, hi = rot_ranges[bone]
            rotations.append(tuple(np.float64(hi) + n * np.float64(lo)))
        elif rate in (0, -8, -4):
            pass
        else:
            raise AnimFormatError(
                f"Unknown rotation bit rate {rate} for bone {bone}")

    frame = AnimFrame()
    if translations:
        frame.translations = np.array(translations, np.float64)
    if rotations:
        frame.rotations = np.array(rotations, np.float64)
    return frame


# ---------------------------------------------------------------------------
# Resolving the mapping indirection
# ---------------------------------------------------------------------------

def resolve(anim: AnimFile, part_index: int = 0) -> ResolvedAnim:
    """Expand one part into dense per-bone tracks (see ResolvedAnim)."""
    part = anim.parts[part_index]
    bones = anim.bone_count
    frames = max(1, len(part.dynamic_frames))

    translations = np.zeros((frames, bones, 3), np.float32)
    rotations = np.zeros((frames, bones, 4), np.float32)
    rotations[:, :, 3] = 1.0        # identity xyzw
    has_t = np.zeros(bones, bool)
    has_r = np.zeros(bones, bool)
    static_t = np.zeros(bones, bool)
    static_r = np.zeros(bones, bool)

    static = part.static_frame or AnimFrame()
    for bone in range(bones):
        tmap = part.translation_mappings[bone]
        if tmap.is_dynamic and part.dynamic_frames:
            has_t[bone] = True
            for f, frame in enumerate(part.dynamic_frames):
                translations[f, bone] = frame.translations[tmap.index]
        elif tmap.is_static:
            has_t[bone] = True
            static_t[bone] = True
            translations[:, bone] = static.translations[tmap.index]

        rmap = part.rotation_mappings[bone]
        if rmap.is_dynamic and part.dynamic_frames:
            has_r[bone] = True
            for f, frame in enumerate(part.dynamic_frames):
                rotations[f, bone] = frame.rotations[rmap.index]
        elif rmap.is_static:
            has_r[bone] = True
            static_r[bone] = True
            rotations[:, bone] = static.rotations[rmap.index]

    return ResolvedAnim(translations=translations, rotations=rotations,
                        has_translation=has_t, has_rotation=has_r,
                        static_translation=static_t,
                        static_rotation=static_r)


def resolve_all(anim: AnimFile) -> ResolvedAnim:
    """Expand every part into one continuous set of dense per-bone tracks.

    Multi-part files only occur in version 8 (see _load_parts_v8); the
    game does not treat parts as alternates or layers, it plays them back
    to back (AnimationClip's constructor in AssetEditor simply
    concatenates every part's frames in order), so that's what this does.
    has_translation/has_rotation are OR'd across parts (a bone counts as
    animated if any part animates it); static_translation/static_rotation
    are recomputed from the concatenated frames rather than trusted from
    individual parts, since a bone can be static within a part but change
    value between parts.
    """
    if len(anim.parts) == 1:
        return resolve(anim, 0)

    resolved_parts = [resolve(anim, i) for i in range(len(anim.parts))]
    translations = np.concatenate([r.translations for r in resolved_parts],
                                  axis=0)
    rotations = np.concatenate([r.rotations for r in resolved_parts], axis=0)
    has_t = np.any([r.has_translation for r in resolved_parts], axis=0)
    has_r = np.any([r.has_rotation for r in resolved_parts], axis=0)
    static_t = np.all(np.isclose(translations, translations[:1]),
                      axis=(0, 2))
    static_r = np.all(np.isclose(rotations, rotations[:1]), axis=(0, 2))
    return ResolvedAnim(translations=translations, rotations=rotations,
                        has_translation=has_t, has_rotation=has_r,
                        static_translation=static_t,
                        static_rotation=static_r)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def _write_string(out: bytearray, text: str):
    raw = text.encode("utf-8")
    if len(raw) > 0xFFFF:
        raise AnimFormatError(f"String too long to store: {text[:40]}...")
    out += struct.pack("<H", len(raw))
    out += raw


def _write_string_utf16(out: bytearray, text: str):
    raw = text.encode("utf-16-le")
    if len(text) > 0xFFFF:
        raise AnimFormatError(f"String too long to store: {text[:40]}...")
    out += struct.pack("<H", len(raw) // 2)
    out += raw


def _write_frame(out: bytearray, frame: AnimFrame, pos_count: int,
                 rot_count: int):
    if len(frame.translations) != pos_count \
            or len(frame.rotations) != rot_count:
        raise AnimFormatError(
            "All frames must have the same translation/rotation counts "
            f"(expected {pos_count}/{rot_count}, got "
            f"{len(frame.translations)}/{len(frame.rotations)})")
    out += np.asarray(frame.translations, "<f4").tobytes()
    # Clamp after scaling, not before.  A stored -32768 reads back as
    # -1.00003, and clipping the float to -1.0 first would write it out
    # as -32767 - a real difference in 11 of Warhammer 3's v7 files.
    # Clamping the scaled value keeps that extreme and still stops a
    # quaternion component slightly over 1.0 from wrapping the int16.
    quats = np.round(np.asarray(frame.rotations, np.float32) * 32767.0)
    out += np.clip(quats, -32768.0, 32767.0).astype("<i2").tobytes()


# Version 8 packing rates.  The uncompressed pair is what a file built
# from scratch uses: no range map, and nothing lost.
_V8_TRANS_RAW = 12
_V8_TRANS_RANGED = 3
_V8_ROT_RAW = 8
_V8_ROT_RANGED = 4
# How far outside its range a value may sit before the range is refitted.
# Only float32 noise from the decode should ever land here.
_V8_RANGE_SLACK = 1e-6


def _v8_rates(part: AnimPart, bone_count: int, index: int) -> tuple:
    """(translation rates, rotation rates) for one part.

    Taken from the file when the part came from one, so it re-saves as
    it was read; otherwise derived from the mappings, choosing the
    uncompressed rates.
    """
    rates = []
    for stored, mappings, raw, label in (
            (part.translation_rates, part.translation_mappings,
             _V8_TRANS_RAW, "translation"),
            (part.rotation_rates, part.rotation_mappings,
             _V8_ROT_RAW, "rotation")):
        if stored is not None:
            if len(stored) != bone_count:
                raise AnimFormatError(
                    f"Part {index}: {len(stored)} {label} rates for "
                    f"{bone_count} bones")
            rates.append(np.asarray(stored, np.int8))
            continue
        derived = np.zeros(bone_count, np.int8)
        for bone, mapping in enumerate(mappings):
            if mapping.is_dynamic:
                derived[bone] = raw
            elif mapping.is_static:
                derived[bone] = -raw
        rates.append(derived)
    return tuple(rates)


def _v8_channel_values(part: AnimPart, rate_array, ranged: int,
                       rotations: bool) -> dict:
    """{bone: (n, width) values} for the bones packed through a range.

    Walks the frames the way the reader does - a bone's entry sits at
    the running count of bones before it that carry data of the same
    kind - so the values line up with the bone the range belongs to.
    """
    out = {}
    for dynamic in (False, True):
        frames = (part.dynamic_frames if dynamic
                  else ([part.static_frame] if part.static_frame else []))
        if not frames:
            continue
        cursor = 0
        for bone, stored in enumerate(rate_array):
            rate = int(stored) if dynamic else -int(stored)
            if rate <= 0:
                continue
            column = cursor
            cursor += 1
            if rate != ranged:
                continue
            rows = [(frame.rotations if rotations else frame.translations)
                    [column] for frame in frames]
            out.setdefault(bone, []).extend(rows)
    return {bone: np.asarray(rows, np.float32)
            for bone, rows in out.items()}


def _v8_fit_range(values: np.ndarray) -> np.ndarray:
    """A (2, width) range that covers `values` exactly.

    The reader decodes byte n as `hi + (n / 127) * lo`, so the base is
    the midpoint and the scale is the half-width.
    """
    low = values.min(axis=0)
    high = values.max(axis=0)
    return np.stack([(high - low) / 2.0, (high + low) / 2.0]).astype(
        np.float32)


def _v8_ranges(part: AnimPart, rates: tuple, index: int) -> tuple:
    """The per-bone range maps for the byte-packed channels.

    A part read from a file keeps the ranges it came with, so it writes
    back as it was - but only while they still hold its values.  Edit a
    bone past the range it was exported with and the range is refitted
    around what is actually there, rather than clipping the animation to
    the old one.  A part built from scratch uses the uncompressed rates
    and needs no ranges at all.
    """
    out = []
    for stored, rate_array, ranged, width, rotations in (
            (part.translation_ranges, rates[0], _V8_TRANS_RANGED, 3, False),
            (part.rotation_ranges, rates[1], _V8_ROT_RANGED, 4, True)):
        needed = _v8_channel_values(part, rate_array, ranged, rotations)
        if not needed:
            out.append(np.zeros((0, 2, width), np.float32))
            continue

        size = max(len(stored) if stored is not None else 0,
                   max(needed) + 1)
        table = np.zeros((size, 2, width), np.float32)
        if stored is not None:
            table[:len(stored)] = np.asarray(stored, np.float32)

        for bone, values in needed.items():
            scale, base = table[bone]
            low = base - np.abs(scale)
            high = base + np.abs(scale)
            if stored is None or np.any(values < low - _V8_RANGE_SLACK) \
                    or np.any(values > high + _V8_RANGE_SLACK):
                table[bone] = _v8_fit_range(values)
        out.append(table)
    return tuple(out)


def _v8_pack_ranged(values, lo, hi) -> bytes:
    """The inverse of the reader's `hi + n * lo`, with n = byte / 127.

    In float64 deliberately.  Some ranges are extreme - Warhammer 3 has
    bones whose base is six million times its scale - and there the
    stored float32 barely holds the byte it came from.  Doing the
    recovery in float32 as well throws away the little that is left and
    lands a byte low; float64 recovers all but the cases where the
    float32 itself has lost the bit.
    """
    scale = np.asarray(lo, np.float64)
    base = np.asarray(hi, np.float64)
    # No vanilla range has a zero scale (checked across every version 8
    # file in Warhammer 3), but a hand-built one could: such a channel
    # cannot say anything but the base value, so 0 is the honest byte.
    safe = np.where(scale == 0.0, 1.0, scale)
    n = np.where(scale == 0.0, 0.0,
                 (np.asarray(values, np.float64) - base) / safe * 127.0)
    return np.clip(np.round(n), -128.0, 127.0).astype(np.int8).tobytes()


def _write_frame_v8(out: bytearray, frame: AnimFrame, rates: tuple,
                    ranges: tuple, dynamic: bool, index: int):
    """One version 8 frame, walking the bones in order as the reader does."""
    trans_rates, rot_rates = rates
    trans_ranges, rot_ranges = ranges
    kind = "dynamic" if dynamic else "static"

    cursor = 0
    for bone, stored in enumerate(trans_rates):
        rate = int(stored) if dynamic else -int(stored)
        if rate not in (_V8_TRANS_RAW, _V8_TRANS_RANGED):
            continue
        if cursor >= len(frame.translations):
            raise AnimFormatError(
                f"Part {index}: the {kind} frame has "
                f"{len(frame.translations)} translations, but the rates "
                f"ask for more")
        value = frame.translations[cursor]
        cursor += 1
        if rate == _V8_TRANS_RAW:
            out += struct.pack("<3f", *value)
        else:
            lo, hi = trans_ranges[bone]
            out += _v8_pack_ranged(value, lo, hi)
    if cursor != len(frame.translations):
        raise AnimFormatError(
            f"Part {index}: the {kind} frame has "
            f"{len(frame.translations)} translations but the rates use "
            f"{cursor}")

    cursor = 0
    for bone, stored in enumerate(rot_rates):
        rate = int(stored) if dynamic else -int(stored)
        if rate not in (_V8_ROT_RAW, _V8_ROT_RANGED):
            continue
        if cursor >= len(frame.rotations):
            raise AnimFormatError(
                f"Part {index}: the {kind} frame has "
                f"{len(frame.rotations)} rotations, but the rates ask "
                "for more")
        value = frame.rotations[cursor]
        cursor += 1
        if rate == _V8_ROT_RAW:
            quat = np.round(np.asarray(value, np.float32) * 32767.0)
            out += np.clip(quat, -32768.0, 32767.0).astype("<i2").tobytes()
        else:
            lo, hi = rot_ranges[bone]
            out += _v8_pack_ranged(value, lo, hi)
    if cursor != len(frame.rotations):
        raise AnimFormatError(
            f"Part {index}: the {kind} frame has {len(frame.rotations)} "
            f"rotations but the rates use {cursor}")


def _save_v8_parts(out: bytearray, anim: AnimFile):
    out += struct.pack("<I", len(anim.parts))
    for index, part in enumerate(anim.parts):
        _encode_v8_part(out, part, anim, index)


def _encode_v8_part(out: bytearray, part: AnimPart, anim: AnimFile,
                    index: int):
    if len(part.translation_mappings) != anim.bone_count \
            or len(part.rotation_mappings) != anim.bone_count:
        raise AnimFormatError(
            f"Part {index}: mapping table lengths must equal the "
            f"bone count ({anim.bone_count})")
    rates = _v8_rates(part, anim.bone_count, index)
    ranges = _v8_ranges(part, rates, index)

    out += rates[0].astype(np.int8).tobytes()
    out += rates[1].astype(np.int8).tobytes()
    out += struct.pack("<II", len(ranges[0]), len(ranges[1]))
    out += ranges[0].astype("<f4").tobytes()
    out += ranges[1].astype("<f4").tobytes()

    static = part.static_frame
    if static is not None:
        out += struct.pack("<II", len(static.translations),
                           len(static.rotations))
        _write_frame_v8(out, static, rates, ranges, False, index)
    else:
        out += struct.pack("<II", 0, 0)

    if part.dynamic_frames:
        first = part.dynamic_frames[0]
        out += struct.pack("<III", len(first.translations),
                           len(first.rotations),
                           len(part.dynamic_frames))
        for frame in part.dynamic_frames:
            _write_frame_v8(out, frame, rates, ranges, True, index)
    else:
        out += struct.pack("<III", 0, 0,
                           0 if part.declared_frame_count is None
                           else part.declared_frame_count)


def _write_event_block(out: bytearray, events: list):
    out += struct.pack("<I", len(events))
    for event in events:
        out += struct.pack("<I", len(event))
        for text in event:
            _write_string_utf16(out, text)


def _save_shogun2(anim: AnimFile, part: AnimPart) -> bytes:
    """Serialize a Shogun 2 file (version 1, or 0 for the headerless
    variant).  Every bone must be dynamic in every frame - neither layout
    has any way to express anything else."""
    bone_count = anim.bone_count
    headerless = anim.version == SHOGUN2_NO_HEADER_VERSION
    floats_per_bone = 10 if headerless else 7

    _require_all_dynamic(part, "Shogun 2")

    out = bytearray()
    if not headerless:
        out += struct.pack("<I", anim.version)
    out += struct.pack("<ff", anim.frame_rate, anim.duration)

    out += struct.pack("<I", bone_count)
    for bone in anim.bones:
        _write_string_utf16(out, bone.name)
        out += struct.pack("<i", bone.parent)

    out += struct.pack("<I", len(part.dynamic_frames))
    # 0.001 is the value CA writes into version 0's three extra floats.
    _write_interleaved_frames(out, part, bone_count, floats_per_bone,
                              extras_default=0.001)

    _write_event_block(out, anim.events)
    for block in anim.extra_event_blocks:
        _write_event_block(out, block)

    return bytes(out)


def save(anim: AnimFile) -> bytes:
    """Serialize any version this module reads.

    Version 8 is the only layout with more than one part; the rest hold
    exactly one.
    """
    if anim.version not in SUPPORTED_WRITE_VERSIONS:
        raise AnimFormatError(
            f"Cannot write .anim version {anim.version} "
            f"(writable: {SUPPORTED_WRITE_VERSIONS})")
    if not anim.parts:
        raise AnimFormatError("An animation needs at least one part")
    if anim.version != 8 and len(anim.parts) != 1:
        raise AnimFormatError(
            f"Cannot write a {len(anim.parts)}-part version "
            f"{anim.version} animation - only version 8 has parts")
    part = anim.parts[0]
    if len(part.translation_mappings) != anim.bone_count \
            or len(part.rotation_mappings) != anim.bone_count:
        raise AnimFormatError("Mapping table lengths must equal bone count")

    if anim.version in (SHOGUN2_VERSION, SHOGUN2_NO_HEADER_VERSION):
        return _save_shogun2(anim, part)

    out = bytearray()
    write_string = (_write_string_utf16
                    if anim.version == UTF16_STRING_VERSION
                    else _write_string)
    out += struct.pack("<IIf", anim.version, anim.header_type,
                       anim.frame_rate)
    write_string(out, anim.skeleton_name)
    if anim.version > 6:
        out += struct.pack("<I", len(anim.flags))
        for flag in anim.flags:
            write_string(out, flag)
    out += struct.pack("<f", anim.duration)

    out += struct.pack("<I", anim.bone_count)
    for bone in anim.bones:
        write_string(out, bone.name)
        out += struct.pack("<i", bone.parent)

    if anim.version == 8:
        out += struct.pack("<I", anim.unknown_v8)
        _save_v8_parts(out, anim)
        return bytes(out)

    if anim.version == UTF16_STRING_VERSION:
        _require_all_dynamic(part, "Version 4")
        _write_v4_flags(out, part.translation_flags, anim.bone_count)
        _write_v4_flags(out, part.rotation_flags, anim.bone_count)
        out += struct.pack("<I", len(part.dynamic_frames))
        _write_interleaved_frames(out, part, anim.bone_count, 7)
        if anim.has_event_block:
            _write_event_block(out, anim.events)
        return bytes(out)

    for mapping in part.translation_mappings:
        out += struct.pack("<i", mapping.value)
    for mapping in part.rotation_mappings:
        out += struct.pack("<i", mapping.value)

    if anim.version == 7:
        if part.static_frame is not None:
            static = part.static_frame
            out += struct.pack("<II", len(static.translations),
                               len(static.rotations))
            _write_frame(out, static, len(static.translations),
                         len(static.rotations))
        else:
            out += struct.pack("<II", 0, 0)
    elif part.static_frame is not None:
        raise AnimFormatError(
            f"Static frames only exist in version 7 files "
            f"(got version {anim.version})")

    if part.dynamic_frames:
        pos_count = len(part.dynamic_frames[0].translations)
        rot_count = len(part.dynamic_frames[0].rotations)
        out += struct.pack("<iii", pos_count, rot_count,
                           len(part.dynamic_frames))
        for frame in part.dynamic_frames:
            _write_frame(out, frame, pos_count, rot_count)
    else:
        # See AnimPart.declared_frame_count: not always 3.
        out += struct.pack("<iii", 0, 0,
                           3 if part.declared_frame_count is None
                           else part.declared_frame_count)

    if anim.has_event_block:
        _write_event_block(out, anim.events)

    return bytes(out)


# ---------------------------------------------------------------------------
# Convenience builder (used by the exporter and tests)
# ---------------------------------------------------------------------------

def build_simple(version: int, skeleton_name: str, frame_rate: float,
                 bones: list, translations: np.ndarray,
                 rotations: np.ndarray, flags: list | None = None,
                 duration: float | None = None) -> AnimFile:
    """AnimFile with every bone dynamic. translations: (frames, bones, 3),
    rotations: (frames, bones, 4) xyzw in game space."""
    translations = np.asarray(translations, np.float32)
    rotations = np.asarray(rotations, np.float32)
    frames, bone_count = translations.shape[:2]
    if bone_count != len(bones) or rotations.shape[:2] != (frames,
                                                           bone_count):
        raise AnimFormatError("translations/rotations shape mismatch")

    anim = AnimFile(version=version, frame_rate=frame_rate,
                    skeleton_name=skeleton_name, flags=list(flags or []))
    anim.duration = (max(0, frames - 1) / frame_rate if duration is None
                     else duration)
    anim.bones = list(bones)

    part = AnimPart()
    part.translation_mappings = [BoneMapping(i) for i in range(bone_count)]
    part.rotation_mappings = [BoneMapping(i) for i in range(bone_count)]
    for f in range(frames):
        part.dynamic_frames.append(AnimFrame(
            translations=translations[f].copy(),
            rotations=rotations[f].copy()))
    anim.parts = [part]
    return anim
