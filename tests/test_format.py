"""Standalone tests for io_scene_rmv2.rmv2_format and .anim_format
(no Blender required).

Run with:  python tests/test_format.py
"""

import os
import struct
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "io_scene_rmv2"))
import anim_format as af  # noqa: E402
import rmv2_format as rf  # noqa: E402


def make_cube_mesh(weight_count=4, bone_a=0, bone_b=3):
    """8-corner cube with per-vertex data covering all channels."""
    corners = np.array([
        [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
        [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
    ], dtype=np.float32) * 0.735  # non-trivial float values

    mesh = rf.RmvMeshData.empty(8, weight_count)
    mesh.positions = corners
    normals = corners / np.linalg.norm(corners, axis=1, keepdims=True)
    mesh.normals = normals.astype(np.float32)
    # arbitrary but unit-ish tangent frame
    t = np.cross(normals, [0.0, 1.0, 0.001])
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    mesh.tangents = t.astype(np.float32)
    b = np.cross(normals, t)
    mesh.binormals = (b / np.linalg.norm(b, axis=1, keepdims=True)
                      ).astype(np.float32)
    mesh.uv0 = np.linspace(0, 1, 16, dtype=np.float32).reshape(8, 2)
    mesh.uv1 = np.linspace(1, 0, 16, dtype=np.float32).reshape(8, 2)
    mesh.colours = np.round(np.linspace(0, 255, 32)).reshape(8, 4) \
        .astype(np.float32) / 255.0
    if weight_count:
        idx = np.zeros((8, weight_count), np.uint8)
        wgt = np.zeros((8, weight_count), np.float32)
        idx[:, 0] = bone_a
        idx[:, 1] = bone_b
        wgt[:, 0] = 0.7
        wgt[:, 1] = 0.3
        mesh.bone_indices = idx
        mesh.bone_weights = wgt
    # 12 triangles of a cube
    quads = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    tris = []
    for q in quads:
        tris += [q[0], q[1], q[2], q[0], q[2], q[3]]
    mesh.indices = np.array(tris, dtype=np.uint16)
    return mesh


def make_material(vertex_format, material_id=rf.MAT_WEIGHTED):
    mat = rf.WeightedMaterial(
        material_id=material_id,
        vertex_format=vertex_format,
        model_name="test_mesh",
        texture_directory="variantmeshes\\test",
        filters="",
        pivot=(0.25, 1.5, -0.75),
    )
    mat.textures = [
        (0, "variantmeshes\\test\\test_diffuse.dds"),
        (1, "variantmeshes\\test\\test_normal.dds"),
        (27, "variantmeshes\\test\\test_base_colour.dds"),
    ]
    mat.attachment_points = [
        rf.RmvAttachmentPoint(name="root", bone_index=0),
        rf.RmvAttachmentPoint(name="spine_0", bone_index=3),
    ]
    mat.int_params = [(rf.INT_PARAM_ALPHA, 1)]
    mat.float_params = [(0, 1.0), (1, 2.5)]
    mat.string_params = [(0, "some_string_param")]
    mat.vec4_params = [(0, (0.0, 0.25, 0.5, 1.0))]
    return mat


def make_file(version=7, vertex_format=rf.VF_CINEMATIC, lods=2):
    rmv = rf.RmvFile(version=version, skeleton_name="humanoid01")
    wc = {rf.VF_CINEMATIC: 4, rf.VF_WEIGHTED: 2}.get(vertex_format, 0)
    for i in range(lods):
        lod = rf.RmvLod(camera_distance=(i + 1) * 40.0, lod_level=i,
                        quality_level=0)
        for _ in range(2):  # two meshes per lod
            model = rf.RmvModel(
                material=make_material(vertex_format),
                mesh=make_cube_mesh(wc),
                render_flag=0,
                shader_name=rf.DEFAULT_SHADER_NAME,
            )
            lod.models.append(model)
        rmv.lods.append(lod)
    return rmv


class TestStructSizes(unittest.TestCase):
    def test_strides(self):
        self.assertEqual(rf.vertex_stride(rf.VF_STATIC, 7), 32)
        self.assertEqual(rf.vertex_stride(rf.VF_STATIC, 8), 32)
        self.assertEqual(rf.vertex_stride(rf.VF_WEIGHTED, 7), 28)
        self.assertEqual(rf.vertex_stride(rf.VF_WEIGHTED, 8), 32)
        self.assertEqual(rf.vertex_stride(rf.VF_CINEMATIC, 7), 32)
        self.assertEqual(rf.vertex_stride(rf.VF_CINEMATIC, 8), 36)
        self.assertEqual(rf.vertex_stride(rf.VF_COLLISION, 7), 24)
        self.assertEqual(rf.vertex_stride(rf.VF_POSITION16, 7), 16)
        self.assertEqual(rf.vertex_stride(rf.VF_CUSTOM_TERRAIN, 7), 36)
        self.assertEqual(rf.vertex_stride(rf.VF_CUSTOM_TERRAIN2, 7), 48)


class TestPositionEncoding(unittest.TestCase):
    def test_high_precision_beats_plain_half(self):
        rng = np.random.default_rng(7)
        pos = (rng.random((500, 3), dtype=np.float32) - 0.5) * 4.0
        enc_hi = rf.encode_position_half4(pos, high_precision=True)
        enc_lo = rf.encode_position_half4(pos, high_precision=False)
        dec_hi = rf._decode_position_half4(enc_hi)
        dec_lo = rf._decode_position_half4(enc_lo)
        err_hi = np.abs(dec_hi - pos).max()
        err_lo = np.abs(dec_lo - pos).max()
        self.assertLess(err_hi, err_lo)
        self.assertLess(err_hi, 2e-3)

    def test_w_is_never_zero(self):
        pos = np.zeros((4, 3), np.float32)
        for hp in (True, False):
            enc = rf.encode_position_half4(pos, high_precision=hp)
            self.assertTrue(np.all(enc[:, 3].astype(np.float32) != 0.0))

    def test_out_of_range_raises(self):
        pos = np.array([[1e6, 0, 0]], np.float32)
        with self.assertRaises(rf.RmvFormatError):
            rf.encode_position_half4(pos)


class TestWeights(unittest.TestCase):
    def test_quantized_weights_sum_to_255(self):
        rng = np.random.default_rng(3)
        w = rng.random((100, 4), dtype=np.float32)
        w /= w.sum(axis=1, keepdims=True)
        q = rf._quantize_weights(w)
        self.assertTrue(np.all(q.sum(axis=1) == 255))


class TestVertexRoundtrip(unittest.TestCase):
    def roundtrip(self, fmt, version):
        wc = {rf.VF_CINEMATIC: 4, rf.VF_WEIGHTED: 2}.get(fmt, 0)
        mesh = make_cube_mesh(wc)
        blob = rf.encode_vertices(mesh, fmt, version)
        stride = rf.vertex_stride(fmt, version)
        self.assertEqual(len(blob), stride * 8)
        out = rf.decode_vertices(blob, 0, 8, stride, fmt, version)

        np.testing.assert_allclose(out.positions, mesh.positions, atol=2e-3)
        np.testing.assert_allclose(out.normals, mesh.normals, atol=1.0 / 100)
        np.testing.assert_allclose(out.uv0, mesh.uv0, atol=1e-3)
        if fmt == rf.VF_STATIC:
            np.testing.assert_allclose(out.uv1, mesh.uv1, atol=1e-3)
        if fmt == rf.VF_STATIC or version == 8:
            np.testing.assert_allclose(out.colours, mesh.colours,
                                       atol=0.51 / 255)
        if wc:
            np.testing.assert_array_equal(out.bone_indices,
                                          mesh.bone_indices)
            np.testing.assert_allclose(out.bone_weights, mesh.bone_weights,
                                       atol=1.0 / 255)

    def test_all_writable_formats(self):
        for version in (5, 6, 7, 8):
            for fmt in (rf.VF_STATIC, rf.VF_WEIGHTED, rf.VF_CINEMATIC):
                with self.subTest(version=version, fmt=fmt):
                    self.roundtrip(fmt, version)


class TestFileRoundtrip(unittest.TestCase):
    def check_file(self, version, fmt):
        rmv = make_file(version=version, vertex_format=fmt)
        blob = rf.save(rmv)
        out = rf.load(blob)

        self.assertEqual(out.version, version)
        self.assertEqual(out.skeleton_name, "humanoid01")
        self.assertEqual(len(out.lods), 2)
        for lod_in, lod_out in zip(rmv.lods, out.lods):
            self.assertEqual(len(lod_out.models), len(lod_in.models))
            self.assertAlmostEqual(lod_out.camera_distance,
                                   lod_in.camera_distance, places=4)
            if version >= 7:
                self.assertEqual(lod_out.lod_level, lod_in.lod_level)
            for m_in, m_out in zip(lod_in.models, lod_out.models):
                np.testing.assert_array_equal(m_out.mesh.indices,
                                              m_in.mesh.indices)
                np.testing.assert_allclose(m_out.mesh.positions,
                                           m_in.mesh.positions, atol=2e-3)
                mat_in, mat_out = m_in.material, m_out.material
                self.assertEqual(mat_out.material_id, mat_in.material_id)
                self.assertEqual(mat_out.model_name, mat_in.model_name)
                self.assertEqual(mat_out.texture_directory,
                                 mat_in.texture_directory)
                self.assertEqual(mat_out.textures, mat_in.textures)
                self.assertEqual(mat_out.int_params, mat_in.int_params)
                self.assertEqual(mat_out.string_params, mat_in.string_params)
                self.assertEqual(
                    [(a.name, a.bone_index)
                     for a in mat_out.attachment_points],
                    [(a.name, a.bone_index)
                     for a in mat_in.attachment_points])
                np.testing.assert_allclose(mat_out.pivot, mat_in.pivot,
                                           atol=1e-6)
                self.assertEqual(m_out.shader_name.rstrip("\0"),
                                 m_in.shader_name.rstrip("\0"))

        # Saving what we loaded must be byte-identical (stable roundtrip).
        blob2 = rf.save(out)
        self.assertEqual(blob, blob2)

    def test_versions_and_formats(self):
        for version in (5, 6, 7, 8):
            for fmt in (rf.VF_STATIC, rf.VF_WEIGHTED, rf.VF_CINEMATIC):
                with self.subTest(version=version, fmt=fmt):
                    self.check_file(version, fmt)

    def test_bad_magic(self):
        with self.assertRaises(rf.RmvFormatError):
            rf.load(b"NOPE" + b"\0" * 200)

    def test_static_normals_xz_swap(self):
        """A +X normal must survive the static format's X/Z swap."""
        mesh = make_cube_mesh(0)
        mesh.normals[:] = np.array([1.0, 0.0, 0.0], np.float32)
        blob = rf.encode_vertices(mesh, rf.VF_STATIC, 7)
        out = rf.decode_vertices(blob, 0, 8, 32, rf.VF_STATIC, 7)
        np.testing.assert_allclose(out.normals,
                                   mesh.normals, atol=1.0 / 100)
        # And the raw bytes must have it in the Z slot (file layout check).
        raw = np.frombuffer(blob, dtype=rf._vertex_dtype(rf.VF_STATIC, 7))
        self.assertEqual(raw["normal"][0][2], 255)   # +1 -> byte 255 in Z
        self.assertEqual(raw["normal"][0][0], 128)   # 0 -> byte 128 in X


class TestOffsets(unittest.TestCase):
    def test_header_layout(self):
        """First mesh offset and section sizes must chain correctly."""
        rmv = make_file(version=8, vertex_format=rf.VF_CINEMATIC)
        blob = rf.save(rmv)
        import struct as st
        magic, version, lod_count = st.unpack_from("<4sII", blob, 0)
        self.assertEqual(magic, b"RMV2")
        self.assertEqual(lod_count, 2)
        first_offsets = []
        pos = 140
        for _ in range(lod_count):
            vals = st.unpack_from("<IIIIfIBBBB", blob, pos)
            first_offsets.append(vals[3])
            pos += 28
        self.assertEqual(first_offsets[0], 140 + 28 * 2)
        # lod1 must start exactly at the end of lod0's sections
        offset = first_offsets[0]
        for _ in range(2):  # meshes in lod0
            section = st.unpack_from("<I", blob, offset + 4)[0]
            offset += section
        self.assertEqual(first_offsets[1], offset)


# ---------------------------------------------------------------------------
# .anim tests
# ---------------------------------------------------------------------------

ANIM_BONES = [af.AnimBone("root", -1), af.AnimBone("spine_0", 0),
              af.AnimBone("spine_1", 1), af.AnimBone("arm_left", 1)]


def make_anim(version=7, frames=3, with_static=False, with_none=False,
              flags=()):
    """4-bone animation; optionally bone 2 static (v7) and bone 3 unmapped.

    Quaternion components are multiples of 1/32767 so the int16 encoding
    is exact and roundtrips can compare with tiny tolerances.
    """
    anim = af.AnimFile(version=version, frame_rate=20.0,
                       skeleton_name="humanoid01", flags=list(flags))
    anim.bones = list(ANIM_BONES)
    anim.duration = (frames - 1) / anim.frame_rate

    part = af.AnimPart()
    dynamic = [0, 1]
    static = []
    unmapped = []
    if with_static:
        static = [2]
    else:
        dynamic.append(2)
    if with_none:
        unmapped = [3]
    else:
        dynamic.append(3)

    def quat(k):
        raw = np.array([3000 + k * 10, -1500, 200, 32000], np.float32)
        q = raw / 32767.0
        return q / 1.0     # not normalized on purpose; format doesn't care

    mappings_t = [af.BoneMapping(af.MAPPING_NONE)] * len(anim.bones)
    mappings_r = [af.BoneMapping(af.MAPPING_NONE)] * len(anim.bones)
    for slot, bone in enumerate(dynamic):
        mappings_t[bone] = af.BoneMapping(slot)
        mappings_r[bone] = af.BoneMapping(slot)
    for slot, bone in enumerate(static):
        mappings_t[bone] = af.BoneMapping(10000 + slot)
        mappings_r[bone] = af.BoneMapping(10000 + slot)
    for bone in unmapped:
        mappings_t[bone] = af.BoneMapping(af.MAPPING_NONE)
        mappings_r[bone] = af.BoneMapping(af.MAPPING_NONE)
    part.translation_mappings = mappings_t
    part.rotation_mappings = mappings_r

    if static:
        part.static_frame = af.AnimFrame(
            translations=np.array([[9.0, 8.0, 7.0]] * len(static),
                                  np.float32),
            rotations=np.array([quat(99)] * len(static), np.float32))

    for f in range(frames):
        translations = np.array(
            [[f + slot, 0.25 * slot, -f] for slot in range(len(dynamic))],
            np.float32)
        rotations = np.array([quat(f + slot)
                              for slot in range(len(dynamic))], np.float32)
        part.dynamic_frames.append(af.AnimFrame(translations=translations,
                                                rotations=rotations))
    anim.parts = [part]
    return anim


class TestAnimRoundtrip(unittest.TestCase):
    def check(self, version, **kwargs):
        anim = make_anim(version=version, **kwargs)
        blob = af.save(anim)
        out = af.load(blob)

        self.assertEqual(out.version, version)
        self.assertEqual(out.skeleton_name, "humanoid01")
        self.assertEqual(out.frame_rate, 20.0)
        self.assertEqual(out.flags, anim.flags)
        self.assertAlmostEqual(out.duration, anim.duration, places=6)
        self.assertEqual([(b.name, b.parent) for b in out.bones],
                         [(b.name, b.parent) for b in anim.bones])
        self.assertEqual(len(out.parts), 1)
        p_in, p_out = anim.parts[0], out.parts[0]
        self.assertEqual([m.value for m in p_out.translation_mappings],
                         [m.value for m in p_in.translation_mappings])
        self.assertEqual([m.value for m in p_out.rotation_mappings],
                         [m.value for m in p_in.rotation_mappings])
        self.assertEqual(len(p_out.dynamic_frames), len(p_in.dynamic_frames))
        for f_in, f_out in zip(p_in.dynamic_frames, p_out.dynamic_frames):
            np.testing.assert_allclose(f_out.translations, f_in.translations,
                                       atol=1e-6)
            np.testing.assert_allclose(f_out.rotations, f_in.rotations,
                                       atol=1e-6)
        if p_in.static_frame is not None:
            np.testing.assert_allclose(p_out.static_frame.translations,
                                       p_in.static_frame.translations,
                                       atol=1e-6)

        # Saving what we loaded must be byte-identical (stable roundtrip).
        self.assertEqual(af.save(out), blob)

    def test_v7_full(self):
        self.check(7, with_static=True, with_none=True,
                   flags=["shake_camera"])

    def test_v7_plain(self):
        self.check(7)

    def test_v6_no_flags_no_static(self):
        self.check(6)

    def test_v5(self):
        self.check(5)

    def test_v4_rejected(self):
        """Real v4 files use UTF-16 strings and a different body layout,
        so they are cleanly refused rather than misparsed."""
        blob = af.save(make_anim(version=7))
        with self.assertRaises(af.AnimFormatError):
            af.load(struct.pack("<I", 4) + blob[4:])
        anim = make_anim(version=7)
        anim.version = 4
        with self.assertRaises(af.AnimFormatError):
            af.save(anim)

    def test_single_frame_skeleton(self):
        """Skeleton files are one-frame animations."""
        self.check(7, frames=1)

    def test_flags_rejected_below_v7(self):
        anim = make_anim(version=6)
        anim.flags = ["nope"]
        blob = af.save(anim)     # flags silently only written for v7+
        self.assertEqual(af.load(blob).flags, [])

    def test_static_frame_rejected_below_v7(self):
        anim = make_anim(version=6)
        anim.parts[0].static_frame = af.AnimFrame(
            translations=np.zeros((1, 3), np.float32),
            rotations=np.zeros((1, 4), np.float32))
        with self.assertRaises(af.AnimFormatError):
            af.save(anim)

    def test_v8_not_writable(self):
        anim = make_anim(version=7)
        anim.version = 8
        with self.assertRaises(af.AnimFormatError):
            af.save(anim)

    def test_bad_version(self):
        with self.assertRaises(af.AnimFormatError):
            af.load(b"RMV2" + b"\0" * 100)

    def test_truncated(self):
        blob = af.save(make_anim())
        with self.assertRaises(af.AnimFormatError):
            af.load(blob[:len(blob) // 2])

    def test_empty_animation_writes_frame_count_3(self):
        """CA writes 0,0,3 for 'no dynamic data'; we keep that quirk."""
        anim = make_anim(frames=1)
        anim.parts[0].dynamic_frames = []
        blob = af.save(anim)
        out = af.load(blob)
        self.assertEqual(len(out.parts[0].dynamic_frames), 0)
        self.assertEqual(struct.unpack_from("<i", blob, len(blob) - 4)[0], 3)


class TestAnimResolve(unittest.TestCase):
    def test_mapping_semantics(self):
        anim = make_anim(version=7, frames=2, with_static=True,
                         with_none=True)
        res = af.resolve(anim)
        self.assertEqual(res.translations.shape, (2, 4, 3))

        # dynamic bones 0/1 -> slots 0/1
        np.testing.assert_allclose(res.translations[1][1],
                                   [2.0, 0.25, -1.0], atol=1e-6)
        self.assertTrue(res.has_translation[0])
        self.assertFalse(res.static_translation[0])

        # static bone 2 -> constant static-frame value on every frame
        for f in range(2):
            np.testing.assert_allclose(res.translations[f][2],
                                       [9.0, 8.0, 7.0], atol=1e-6)
        self.assertTrue(res.static_translation[2])

        # unmapped bone 3 -> identity + has_* False
        self.assertFalse(res.has_translation[3])
        self.assertFalse(res.has_rotation[3])
        np.testing.assert_allclose(res.translations[0][3], [0, 0, 0])
        np.testing.assert_allclose(res.rotations[0][3], [0, 0, 0, 1])

    def test_build_simple_matches_resolve(self):
        translations = np.arange(2 * 3 * 3, dtype=np.float32) \
            .reshape(2, 3, 3)
        rotations = np.zeros((2, 3, 4), np.float32)
        rotations[:, :, 3] = 1.0
        anim = af.build_simple(7, "humanoid01", 20.0, ANIM_BONES[:3],
                               translations, rotations)
        res = af.resolve(anim)
        np.testing.assert_allclose(res.translations, translations)
        self.assertTrue(res.has_translation.all())
        self.assertAlmostEqual(anim.duration, 0.05)
        out = af.load(af.save(anim))
        np.testing.assert_allclose(
            af.resolve(out).translations, translations, atol=1e-6)


class TestAnimV8(unittest.TestCase):
    def _header(self, bone_specs):
        out = bytearray()
        out += struct.pack("<IIf", 8, 1, 20.0)
        out += struct.pack("<H", 2) + b"sk"          # skeleton name
        out += struct.pack("<I", 0)                  # flag count
        out += struct.pack("<f", 0.1)                # duration
        out += struct.pack("<I", len(bone_specs))
        for name, parent in bone_specs:
            raw = name.encode()
            out += struct.pack("<H", len(raw)) + raw
            out += struct.pack("<i", parent)
        out += struct.pack("<I", 77)                 # unknown v8 field
        return out

    def test_full_rate_static_and_dynamic(self):
        out = self._header([("a", -1), ("b", 0)])
        out += struct.pack("<I", 1)                  # part count
        out += struct.pack("<bb", 12, -12)           # trans rates
        out += struct.pack("<bb", 8, -8)             # rot rates
        out += struct.pack("<II", 0, 0)              # range map lengths
        out += struct.pack("<II", 1, 1)              # static counts
        out += struct.pack("<3f", 5.0, 6.0, 7.0)     # static trans (bone b)
        out += struct.pack("<4h", 0, 0, 0, 32767)    # static rot
        out += struct.pack("<III", 1, 1, 2)          # dyn counts + frames
        for f in range(2):
            out += struct.pack("<3f", 1.0 + f, 2.0, 3.0)
            out += struct.pack("<4h", 16384, 0, 0, 16384)

        anim = af.load(bytes(out))
        self.assertEqual(anim.version, 8)
        self.assertEqual(anim.unknown_v8, 77)
        self.assertEqual(len(anim.parts), 1)
        part = anim.parts[0]
        self.assertTrue(part.translation_mappings[0].is_dynamic)
        self.assertTrue(part.translation_mappings[1].is_static)
        self.assertEqual(len(part.dynamic_frames), 2)

        res = af.resolve(anim)
        np.testing.assert_allclose(res.translations[1][0], [2.0, 2.0, 3.0],
                                   atol=1e-6)
        np.testing.assert_allclose(res.translations[0][1], [5.0, 6.0, 7.0],
                                   atol=1e-6)
        np.testing.assert_allclose(res.rotations[0][1], [0, 0, 0, 1.0],
                                   atol=1e-4)
        np.testing.assert_allclose(res.rotations[0][0],
                                   [0.5, 0, 0, 0.5], atol=1e-4)

    def test_ranged_encodings(self):
        out = self._header([("a", -1)])
        out += struct.pack("<I", 1)                  # part count
        out += struct.pack("<b", 3)                  # trans rate: ranged
        out += struct.pack("<b", 4)                  # rot rate: ranged
        out += struct.pack("<II", 1, 1)              # range map lengths
        out += struct.pack("<6f", 1.0, 1.0, 1.0, 2.0, 2.0, 2.0)  # min, max
        out += struct.pack("<8f", 0.5, 0.5, 0.5, 0.5, 0.0, 0.0, 0.0, 0.5)
        out += struct.pack("<II", 0, 0)              # static counts
        out += struct.pack("<III", 1, 1, 1)          # dyn counts + frames
        out += struct.pack("<3b", 127, 0, -127)      # trans bytes
        out += struct.pack("<4b", 127, -127, 0, 127)  # rot bytes

        anim = af.load(bytes(out))
        res = af.resolve(anim)
        # decode is max + (byte/127) * min (AssetEditor's formula)
        np.testing.assert_allclose(res.translations[0][0], [3.0, 2.0, 1.0],
                                   atol=1e-5)
        np.testing.assert_allclose(res.rotations[0][0],
                                   [0.5, -0.5, 0.0, 1.0], atol=1e-5)

    def test_trailing_bytes_rejected(self):
        blob = af.save(make_anim())
        with self.assertRaises(af.AnimFormatError):
            af.load(blob + b"\0\0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
