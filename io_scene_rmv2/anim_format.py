"""Binary reader/writer for Creative Assembly's .anim skeleton/animation
format.

Like rmv2_format, this module is intentionally free of any Blender (bpy)
dependencies so it can be unit-tested with a plain Python interpreter.

The implementation follows the C# reference in
TheAssetEditor/Shared/GameFiles/Animation/AnimationFile.cs:

    header      version (u32), always-one (u32), frame rate (f32),
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
splits the data into parts with per-bone bit-rate compression (read-only
here, same as AssetEditor).

Skeleton files (animations/skeletons/*.anim) are ordinary .anim files
carrying a few identical copies of the bind pose as dynamic frames -
they are structurally indistinguishable from short animations.

Coordinates are in the game's space (right-handed, Y-up); conversion to
Blender space happens elsewhere.  All data is little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

# Version 4 (pre-Rome 2) is deliberately unsupported: real v4 files use
# UTF-16 strings and a different body layout, so they cannot be read with
# the v5+ structure below (AssetEditor's reader has the same limitation).
SUPPORTED_READ_VERSIONS = (5, 6, 7, 8)
SUPPORTED_WRITE_VERSIONS = (5, 6, 7)

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
    """One frame: (n,3) float32 translations and (n,4) float32 xyzw quats
    (already divided by 32767)."""
    translations: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32))
    rotations: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 4), np.float32))


@dataclass
class AnimPart:
    translation_mappings: list = field(default_factory=list)  # [BoneMapping]
    rotation_mappings: list = field(default_factory=list)     # [BoneMapping]
    static_frame: AnimFrame | None = None
    dynamic_frames: list = field(default_factory=list)        # [AnimFrame]


@dataclass
class AnimFile:
    version: int = 7
    frame_rate: float = 20.0
    skeleton_name: str = ""
    flags: list = field(default_factory=list)       # v7+ flag strings
    duration: float = 0.0                           # total playtime, seconds
    unknown_v8: int = 0
    bones: list = field(default_factory=list)       # [AnimBone]
    parts: list = field(default_factory=list)       # [AnimPart]

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

    def bytes_(self, n: int) -> bytes:
        self._need(n)
        raw = self.data[self.off:self.off + n]
        self.off += n
        return raw

    @property
    def remaining(self) -> int:
        return len(self.data) - self.off


_QUAT_SCALE = 1.0 / 32767.0


def _read_frame(r: _Reader, pos_count: int, rot_count: int) -> AnimFrame:
    frame = AnimFrame()
    if pos_count:
        raw = np.frombuffer(r.bytes_(pos_count * 12), dtype="<f4")
        frame.translations = raw.reshape(pos_count, 3).astype(np.float32)
    if rot_count:
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
        raise AnimFormatError(
            f"Unsupported .anim version {anim.version} - is this really "
            f"a .anim file? (supported: {SUPPORTED_READ_VERSIONS})")
    always_one = r.u32()    # noqa: F841  (type field, always 1)
    anim.frame_rate = r.f32()
    anim.skeleton_name = r.string()

    if anim.version > 6:
        flag_count = r.u32()
        if flag_count > 1000:
            raise AnimFormatError(f"Implausible flag count {flag_count}")
        anim.flags = [r.string() for _ in range(flag_count)]

    anim.duration = r.f32()

    bone_count = r.u32()
    if bone_count > 100000:
        raise AnimFormatError(f"Implausible bone count {bone_count}")
    for i in range(bone_count):
        name = r.string()
        parent = r.i32()
        anim.bones.append(AnimBone(name=name, parent=parent))

    if anim.version == 8:
        anim.unknown_v8 = r.u32()
        anim.parts = _load_parts_v8(r, bone_count)
    else:
        anim.parts = [_load_part_default(r, bone_count, anim.version)]

    if r.remaining:
        raise AnimFormatError(
            f"{r.remaining} unparsed bytes at the end of the file")
    return anim


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
    frame_count = r.i32()   # 3 when there is no data, for unknown reasons
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
        if dyn_pos or dyn_rot:
            for _ in range(frame_count):
                part.dynamic_frames.append(_read_frame_v8(
                    r, trans_rates, rot_rates, trans_ranges, rot_ranges,
                    dynamic=True))
        parts.append(part)
    return parts


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
            n = np.frombuffer(r.bytes_(3), np.int8).astype(np.float32) / 127.0
            lo, hi = trans_ranges[bone]
            translations.append(tuple(hi + n * lo))
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
            n = np.frombuffer(r.bytes_(4), np.int8).astype(np.float32) / 127.0
            lo, hi = rot_ranges[bone]
            rotations.append(tuple(hi + n * lo))
        elif rate in (0, -8, -4):
            pass
        else:
            raise AnimFormatError(
                f"Unknown rotation bit rate {rate} for bone {bone}")

    frame = AnimFrame()
    if translations:
        frame.translations = np.array(translations, np.float32)
    if rotations:
        frame.rotations = np.array(rotations, np.float32)
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


def _write_frame(out: bytearray, frame: AnimFrame, pos_count: int,
                 rot_count: int):
    if len(frame.translations) != pos_count \
            or len(frame.rotations) != rot_count:
        raise AnimFormatError(
            "All frames must have the same translation/rotation counts "
            f"(expected {pos_count}/{rot_count}, got "
            f"{len(frame.translations)}/{len(frame.rotations)})")
    out += np.asarray(frame.translations, "<f4").tobytes()
    quats = np.clip(np.asarray(frame.rotations, np.float32), -1.0, 1.0)
    out += np.round(quats * 32767.0).astype("<i2").tobytes()


def save(anim: AnimFile) -> bytes:
    """Serialize. Only versions 5-7 with a single part can be written
    (same limitation as AssetEditor)."""
    if anim.version not in SUPPORTED_WRITE_VERSIONS:
        raise AnimFormatError(
            f"Cannot write .anim version {anim.version} "
            f"(writable: {SUPPORTED_WRITE_VERSIONS})")
    if len(anim.parts) != 1:
        raise AnimFormatError(
            f"Cannot write a {len(anim.parts)}-part animation "
            "(exactly one part required)")
    part = anim.parts[0]
    if len(part.translation_mappings) != anim.bone_count \
            or len(part.rotation_mappings) != anim.bone_count:
        raise AnimFormatError("Mapping table lengths must equal bone count")

    out = bytearray()
    out += struct.pack("<IIf", anim.version, 1, anim.frame_rate)
    _write_string(out, anim.skeleton_name)
    if anim.version > 6:
        out += struct.pack("<I", len(anim.flags))
        for flag in anim.flags:
            _write_string(out, flag)
    out += struct.pack("<f", anim.duration)

    out += struct.pack("<I", anim.bone_count)
    for bone in anim.bones:
        _write_string(out, bone.name)
        out += struct.pack("<i", bone.parent)

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
        out += struct.pack("<iii", 0, 0, 3)     # 3: quirk kept from CA files

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
