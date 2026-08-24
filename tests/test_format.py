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
import arm_format as armf  # noqa: E402
import rmv2_format as rf  # noqa: E402
import utils  # noqa: E402
import vmpf_format as vf  # noqa: E402
import vwm_format as wf  # noqa: E402


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
    # 12 triangles of a cube (wound so cross((v1-v0),(v2-v0)) matches the
    # outward per-vertex normal above - verified against real RMV2 assets,
    # which need no winding reversal on import/export)
    quads = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    tris = []
    for q in quads:
        tris += [q[2], q[1], q[0], q[3], q[2], q[0]]
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


class TestRmv2Version5(unittest.TestCase):
    """Rome 2's first version writes every fixed-width string as UTF-16 at
    twice the width.  Field order and count are v6's, so the difference is
    entirely one of widths and codec."""

    def test_roundtrip(self):
        rmv = make_file(version=5, vertex_format=rf.VF_WEIGHTED)
        blob = rf.save(rmv)
        out = rf.load(blob)
        self.assertEqual(out.version, 5)
        self.assertEqual(out.skeleton_name, "humanoid01")
        mat = out.lods[0].models[0].material
        self.assertEqual(mat.model_name, "test_mesh")
        self.assertEqual(mat.texture_directory, "variantmeshes\\test")
        self.assertEqual(len(mat.textures), 3)
        self.assertEqual([a.name for a in mat.attachment_points],
                         ["root", "spine_0"])
        self.assertEqual(rf.save(out), blob)

    def test_strings_are_utf16(self):
        blob = rf.save(make_file(version=5, vertex_format=rf.VF_STATIC))
        self.assertIn("humanoid01".encode("utf-16-le"), blob)
        self.assertNotIn(b"humanoid01", blob)
        self.assertIn("test_mesh".encode("utf-16-le"), blob)

    def test_field_widths(self):
        """The header, common header and material all double."""
        self.assertEqual(rf._file_header_struct(5).size, 268)
        self.assertEqual(rf._file_header_struct(6).size, 140)
        self.assertEqual(rf._common_header_struct(5).size, 112)
        self.assertEqual(rf._common_header_struct(6).size, 80)
        self.assertEqual(rf._weighted_material_struct(5).size, 1404)
        self.assertEqual(rf._weighted_material_struct(6).size, 860)
        self.assertEqual(rf._attachment_point_struct(5).size, 116)
        self.assertEqual(rf._texture_struct(5).size, 516)

    def test_v6_is_unaffected(self):
        blob = rf.save(make_file(version=6, vertex_format=rf.VF_STATIC))
        self.assertIn(b"humanoid01", blob)
        self.assertEqual(rf.save(rf.load(blob)), blob)

    def test_a_v5_file_can_be_written_as_v6(self):
        """Re-versioning across the codec change has to re-encode every
        string, not copy bytes."""
        rmv = rf.load(rf.save(make_file(version=5,
                                        vertex_format=rf.VF_WEIGHTED)))
        rmv.version = 6
        out = rf.load(rf.save(rmv))
        self.assertEqual(out.version, 6)
        self.assertEqual(out.skeleton_name, "humanoid01")
        self.assertEqual(out.lods[0].models[0].material.model_name,
                         "test_mesh")


class TestRmv2Version3(unittest.TestCase):
    """Version 3 is Shogun 2's layout, kept for Rome 2's interface models:
    no skeleton name in the header, a UTF-16 one after the LOD table, and
    a material that is a run of fixed-width fields."""

    def test_roundtrip(self):
        blob = rf.save(make_s2_file(version=3))
        out = rf.load(blob)
        self.assertEqual(out.version, 3)
        self.assertTrue(out.is_shogun2)
        self.assertEqual(out.skeleton_name, "turtle_ship.anim")
        self.assertEqual(rf.save(out), blob)

    def test_shader_name_is_kept_like_v2(self):
        """v1 materials start with the model name; v2 and v3 put a shader
        name in front of it."""
        out = rf.load(rf.save(make_s2_file(version=3)))
        mat = out.lods[0].models[0].material
        self.assertEqual(mat.shader_name, "rigid_default")
        self.assertEqual(mat.model_name, "hull")


LAYOUT_VERTICES = 6


def make_layout_block(vertex_format, count=LAYOUT_VERTICES):
    """A plausible vertex block for one of the Rome 2-era layouts:
    positions inside a small box, unit-length tangent frames, uvs in
    [0,1], and whatever extra channels the layout carries."""
    rng = np.random.default_rng(7)
    dt = rf._vertex_dtype(vertex_format, 6)
    raw = np.zeros(count, dt)

    def unit():
        v = rng.normal(size=(count, 3))
        return v / np.linalg.norm(v, axis=1)[:, None]

    raw["pos"][:, :3] = rng.uniform(-4.0, 4.0, (count, 3))
    raw["pos"][:, 3] = 1.0
    for name in ("normal", "tangent", "binormal"):
        if name not in dt.names:
            continue
        if dt[name].subdtype[0] == np.dtype("u1"):
            raw[name] = rf._encode_byte_vec(unit())
        else:
            raw[name][:, :3] = unit()
    if "uv" in dt.names:
        raw["uv"] = rng.uniform(0.0, 1.0, (count, 2))
    if "pivot" in dt.names:
        raw["pivot"][:, :3] = rng.uniform(-1.0, 1.0, (count, 3))
        raw["pivot"][:, 3] = 1.0
    if "wind" in dt.names:
        raw["wind"] = rng.uniform(0.0, 2.0, (count, 8))
    if "unknown" in dt.names:
        raw["unknown"] = rng.uniform(0.0, 1.0, (count, 4))
    return raw.tobytes()


class TestRome2VertexLayouts(unittest.TestCase):
    """The layouts Rome 2 added for vegetation, grass, water and terrain
    tiles.  The two channels a vegetation vertex carries beyond the usual
    ones live in RmvMeshData.extras, which is what lets these be written
    and not only read."""

    LAYOUTS = {rf.VF_VEGETATION: 60, rf.VF_TREE_BILLBOARD: 28,
               rf.VF_GRASS: 28, rf.VF_POSITION_UV: 12,
               rf.VF_POSITION_HALF: 8}

    def test_strides(self):
        for fmt, stride in self.LAYOUTS.items():
            with self.subTest(fmt=rf.VERTEX_FORMAT_NAMES[fmt]):
                self.assertEqual(rf.vertex_stride(fmt, 6), stride)

    def test_declared_format_pairs_are_unambiguous(self):
        """Three different layouts come in at 28 bytes, so the declared
        format has to be part of the key."""
        self.assertEqual(
            rf._FORMAT_BY_DECLARED_STRIDE[(rf.VF_POSITION16, 28)],
            rf.VF_GRASS)
        self.assertEqual(
            rf._FORMAT_BY_DECLARED_STRIDE[(rf.VF_ROME2_TREE, 28)],
            rf.VF_TREE_BILLBOARD)
        self.assertEqual(rf.vertex_stride(rf.VF_WEIGHTED, 6), 28)

    def test_decode_reads_the_geometry(self):
        """A hand-built vegetation block: the position is the second
        half4, not the first."""
        dt = rf._vertex_dtype(rf.VF_VEGETATION, 6)
        raw = np.zeros(2, dt)
        raw["pos"][:, 0:3] = [[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]]
        raw["pos"][:, 3] = 1.0
        raw["pivot"][:, 0:3] = 9.0
        raw["normal"][:, 0:3] = [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        raw["uv"] = [[0.25, 0.5], [0.75, 1.0]]
        blob = raw.tobytes()
        mesh = rf.decode_vertices(blob, 0, 2, 60, rf.VF_VEGETATION, 6)
        np.testing.assert_allclose(mesh.positions,
                                   [[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]])
        np.testing.assert_allclose(mesh.normals,
                                   [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        np.testing.assert_allclose(mesh.uv0, [[0.25, 0.5], [0.75, 1.0]])
        # unmodified, it goes back out as the bytes it came in as
        self.assertEqual(rf.encode_vertices(mesh, rf.VF_VEGETATION, 6), blob)

    def test_they_write_as_well_as_read(self):
        """A mesh with no extras at all still encodes - the channels it
        does not have are written as zeros, which is what a vegetation
        mesh built in Blender from scratch would want."""
        mesh = make_cube_mesh(0)
        for fmt, stride in self.LAYOUTS.items():
            with self.subTest(fmt=rf.VERTEX_FORMAT_NAMES[fmt]):
                blob = rf.encode_vertices(mesh, fmt, 6)
                self.assertEqual(len(blob), stride * mesh.vertex_count)

    def test_a_decoded_block_re_encodes_exactly(self):
        """Decode then encode has to be the identity, or a mesh that has
        been through Blender comes back changed for no reason."""
        for fmt, stride in self.LAYOUTS.items():
            with self.subTest(fmt=rf.VERTEX_FORMAT_NAMES[fmt]):
                blob = make_layout_block(fmt)
                mesh = rf.decode_vertices(blob, 0, LAYOUT_VERTICES, stride,
                                          fmt, 6)
                mesh.raw_block = None       # no passthrough: really encode
                self.assertEqual(rf.encode_vertices(mesh, fmt, 6), blob)

    def test_the_extra_channels_survive(self):
        blob = make_layout_block(rf.VF_VEGETATION)
        mesh = rf.decode_vertices(blob, 0, LAYOUT_VERTICES, 60,
                                  rf.VF_VEGETATION, 6)
        self.assertEqual(sorted(mesh.extras), ["pivot", "wind"])
        self.assertEqual(mesh.extras["pivot"].shape, (LAYOUT_VERTICES, 4))
        self.assertEqual(mesh.extras["wind"].shape, (LAYOUT_VERTICES, 8))
        self.assertEqual(rf.extra_channels(rf.VF_VEGETATION),
                         {"pivot": 4, "wind": 8})
        self.assertEqual(rf.extra_channels(rf.VF_STATIC), {})

    def test_a_mesh_that_lost_its_extras_still_writes(self):
        """Zeros, not a crash - and the geometry is unaffected."""
        blob = make_layout_block(rf.VF_VEGETATION)
        mesh = rf.decode_vertices(blob, 0, LAYOUT_VERTICES, 60,
                                  rf.VF_VEGETATION, 6)
        mesh.raw_block = None
        mesh.extras = {}
        out = rf.encode_vertices(mesh, rf.VF_VEGETATION, 6)
        again = rf.decode_vertices(out, 0, LAYOUT_VERTICES, 60,
                                   rf.VF_VEGETATION, 6)
        np.testing.assert_allclose(again.positions, mesh.positions)
        self.assertFalse(again.extras["pivot"].any())
        self.assertFalse(again.extras["wind"].any())


class TestEveryLayoutWrites(unittest.TestCase):
    """Every vertex layout this module reads, it can also write.  The
    channels that have no standard field - a bow wave's second position,
    custom terrain's spare colour channels, a vegetation vertex's wind
    weights - travel in RmvMeshData.extras."""

    READABLE = (rf.VF_STATIC, rf.VF_COLLISION, rf.VF_POSITION16,
                rf.VF_CUSTOM_TERRAIN, rf.VF_CUSTOM_TERRAIN2,
                rf.VF_POSITION_UV, rf.VF_S2_STATIC_NO_UV2,
                rf.VF_S2_STATIC_FLOAT, rf.VF_S2_BOW_WAVE,
                rf.VF_VEGETATION, rf.VF_TREE_BILLBOARD, rf.VF_GRASS,
                rf.VF_POSITION_HALF, rf.VF_SWAY)

    def test_all_of_them(self):
        mesh = make_cube_mesh(0)
        for fmt in self.READABLE:
            with self.subTest(fmt=rf.VERTEX_FORMAT_NAMES[fmt]):
                blob = rf.encode_vertices(mesh, fmt, 6)
                self.assertEqual(
                    len(blob), rf.vertex_stride(fmt, 6) * mesh.vertex_count)

    def test_bow_wave_second_position_survives(self):
        dt = rf._vertex_dtype(rf.VF_S2_BOW_WAVE, 2)
        raw = np.zeros(3, dt)
        raw["pos"][:, :3] = [[1.0, 2.0, 3.0]] * 3
        raw["pos"][:, 3] = 1.0
        raw["pos2"][:, :3] = [[4.0, 5.0, 6.0]] * 3
        raw["pos2"][:, 3] = 1.0
        raw["uv"] = 0.5
        raw["unknown"] = 0.25
        blob = raw.tobytes()
        mesh = rf.decode_vertices(blob, 0, 3, dt.itemsize,
                                  rf.VF_S2_BOW_WAVE, 2)
        np.testing.assert_allclose(mesh.extras["pos2"][:, :3],
                                   [[4.0, 5.0, 6.0]] * 3)
        mesh.raw_block = None
        self.assertEqual(rf.encode_vertices(mesh, rf.VF_S2_BOW_WAVE, 2),
                         blob)

    def test_custom_terrain_spare_colours_survive(self):
        dt = rf._vertex_dtype(rf.VF_CUSTOM_TERRAIN2, 6)
        raw = np.zeros(2, dt)
        raw["pos"][:, 3] = 1.0
        raw["normal"][:, 3] = 1.0
        raw["col0"] = 10
        raw["col1"] = 20
        raw["col2"] = 30
        blob = raw.tobytes()
        mesh = rf.decode_vertices(blob, 0, 2, dt.itemsize,
                                  rf.VF_CUSTOM_TERRAIN2, 6)
        self.assertTrue((mesh.extras["col1"] == 20).all())
        mesh.raw_block = None
        self.assertEqual(rf.encode_vertices(mesh, rf.VF_CUSTOM_TERRAIN2, 6),
                         blob)


class TestFixedFieldJunk(unittest.TestCase):
    """CA's fixed-width fields often keep bytes of whatever they last held
    after the terminator.  Re-padding them with zeros loses data that is
    in the file, so an unedited field goes back verbatim."""

    def stamped(self):
        """A v6 file with a junk byte after the shader name's terminator,
        the way Rome 2's tree billboards ship."""
        blob = bytearray(rf.save(make_file(version=6,
                                           vertex_format=rf.VF_STATIC)))
        at = blob.index(rf.DEFAULT_SHADER_NAME.encode())
        blob[at + len(rf.DEFAULT_SHADER_NAME) + 1] = 0x18
        return bytes(blob), at

    def test_shader_name_junk_survives(self):
        blob, _ = self.stamped()
        model = rf.load(blob)
        self.assertEqual(model.lods[0].models[0].shader_name,
                         rf.DEFAULT_SHADER_NAME)
        self.assertEqual(rf.save(model), blob)

    def test_editing_the_name_drops_the_junk(self):
        blob, at = self.stamped()
        model = rf.load(blob)
        for lod in model.lods:
            for m in lod.models:
                m.shader_name = "custom"
        out = rf.save(model)
        self.assertNotIn(bytes([0x18]), out[at:at + 12])

    def test_custom_terrain_path_junk_survives(self):
        mat = rf.CustomTerrainMaterial(texture_path="terrain/tile")
        blob = bytearray(mat.write())
        blob[len("terrain/tile") + 2] = 0x7A
        again = rf.CustomTerrainMaterial.parse(bytes(blob), 0)
        self.assertEqual(again.texture_path, "terrain/tile")
        self.assertEqual(again.write(), bytes(blob))


class TestSwayVertex(unittest.TestCase):
    """Warhammer's wind-sway layout: 20 bytes that declare vertex format
    12, which is not a stride this module can size on its own."""

    def build(self, count=3):
        dt = rf._vertex_dtype(rf.VF_SWAY, 7)
        raw = np.zeros(count, dt)
        raw["pos"] = [[1.0, 2.0, 3.0]] * count
        raw["u"] = 0.25
        raw["normal"] = [[0.0, 1.0, 0.0]] * count
        raw["v"] = 0.75
        raw["col"] = [[255, 255, 255, 128]] * count
        return dt, raw

    def test_stride(self):
        self.assertEqual(rf.vertex_stride(rf.VF_SWAY, 7), 20)

    def test_only_the_declared_pair_identifies_it(self):
        """Nothing else in the series is 20 bytes wide, but the id 12 on
        its own says nothing - the pairing is the key."""
        self.assertEqual(
            rf._FORMAT_BY_DECLARED_STRIDE[(rf.VF_SWAY_DECLARED, 20)],
            rf.VF_SWAY)
        self.assertEqual(rf._FORMAT_BY_STRIDE[20], rf.VF_SWAY)
        with self.assertRaises(rf.RmvFormatError):
            rf.vertex_stride(rf.VF_SWAY_DECLARED, 7)

    def test_the_uv_lives_in_two_different_words(self):
        """U is the W of the position half4 and V the W of the normal
        one, so a naive read of either as a scale is wrong."""
        dt, raw = self.build()
        mesh = rf.decode_vertices(raw.tobytes(), 0, 3, 20, rf.VF_SWAY, 7)
        np.testing.assert_allclose(mesh.uv0, [[0.25, 0.75]] * 3)
        # The position is NOT multiplied by the 0.25 sitting in its W.
        np.testing.assert_allclose(mesh.positions, [[1.0, 2.0, 3.0]] * 3)
        np.testing.assert_allclose(mesh.normals, [[0.0, 1.0, 0.0]] * 3)

    def test_the_sway_weight_rides_in_the_alpha(self):
        dt, raw = self.build()
        mesh = rf.decode_vertices(raw.tobytes(), 0, 3, 20, rf.VF_SWAY, 7)
        np.testing.assert_allclose(mesh.colours[:, 3], 128.0 / 255.0,
                                   atol=1e-6)

    def test_a_decoded_block_re_encodes_byte_for_byte(self):
        dt, raw = self.build()
        blob = raw.tobytes()
        mesh = rf.decode_vertices(blob, 0, 3, 20, rf.VF_SWAY, 7)
        mesh.raw_block = None          # force a real encode
        self.assertEqual(rf.encode_vertices(mesh, rf.VF_SWAY, 7), blob)


class TestNamedMaterial(unittest.TestCase):
    """The 3D interface banners, material ids 29 and 30: a 256-byte model
    name and eight words that are zero in every vanilla file."""

    NAME = "army_banner_strength_fill_lod1"

    def build(self, material_id=30, values=(0,) * 8):
        return rf.NamedMaterial(material_id=material_id,
                                model_name=self.NAME, values=values)

    def test_size(self):
        self.assertEqual(self.build().compute_size(7), 288)
        # v5 doubles the name, not the words
        self.assertEqual(self.build().compute_size(5), 544)

    def test_roundtrip(self):
        for version in (5, 7):
            with self.subTest(version=version):
                mat = self.build()
                blob = mat.write(version)
                self.assertEqual(len(blob), mat.compute_size(version))
                again = rf.NamedMaterial.parse(blob, 0, 30, len(blob),
                                               version)
                self.assertEqual(again.model_name, self.NAME)
                self.assertEqual(again.values, mat.values)
                self.assertEqual(again.write(version), blob)

    def test_a_word_that_is_not_zero_survives(self):
        """No vanilla file sets one, so the writer must not assume."""
        mat = self.build(values=(0, 0, 7, 0, 0, 0, 0, 0))
        again = rf.NamedMaterial.parse(mat.write(7), 0, 30, 288, 7)
        self.assertEqual(again.values[2], 7)

    def test_junk_after_the_terminator_is_kept(self):
        raw = bytearray(288)
        raw[:len(self.NAME)] = self.NAME.encode()
        raw[len(self.NAME) + 1:len(self.NAME) + 6] = b"stale"
        mat = rf.NamedMaterial.parse(bytes(raw), 0, 30, 288, 7)
        self.assertEqual(mat.model_name, self.NAME)
        self.assertEqual(mat.write(7), bytes(raw))

    def test_the_parser_dispatches_by_id_and_size(self):
        raw = bytes(288)
        for material_id in rf._NAMED_MATERIAL_IDS:
            mat = rf._parse_material(raw, 0, material_id, 288, 7)
            self.assertIsInstance(mat, rf.NamedMaterial)
        # The same 288 bytes under any other id are not this material:
        # they fall through to the weighted layout, which does not fit.
        with self.assertRaises(rf.RmvFormatError):
            rf._parse_material(bytes(4096), 0, 68, 288, 7)

    def test_it_declares_no_vertex_format(self):
        """Which is why _load_model has to fall back to the stride."""
        self.assertLess(self.build().vertex_format, 0)
        self.assertEqual(rf._FORMAT_BY_STRIDE[28], rf.VF_S2_STATIC_NO_UV2)


class TestIndicesFirstSection(unittest.TestCase):
    """Warhammer 3's generated tree billboards lay a mesh section out the
    other way round - material, index block, vertices - and declare a
    section size that stops before the vertices, which run past the end
    of their own section to the end of the file."""

    def build(self):
        rmv = rf.RmvFile(version=8, skeleton_name="tree")
        lod = rf.RmvLod(camera_distance=5000.0, lod_level=2)
        mesh = make_cube_mesh(0)
        mat = rf.WeightedMaterial(material_id=89,
                                  vertex_format=rf.VF_POSITION_UV,
                                  model_name="generated_billboard")
        mat.declared_vertex_format = rf.VF_ROME2_TREE
        model = rf.RmvModel(material=mat, mesh=mesh, indices_first=True)
        lod.models.append(model)
        rmv.lods.append(lod)
        return rmv, model

    def test_the_blocks_are_written_in_that_order(self):
        rmv, model = self.build()
        blob = rf.save(rmv)
        common = rf._common_header_struct(8)
        start = rf._file_header_struct(8).size + rf._lod_header_struct(8).size
        _, _, _, voff, vcount, ioff, icount = \
            common.unpack_from(blob, start)[0:7]
        self.assertLess(ioff, voff)
        self.assertEqual(ioff, common.size + model.material.compute_size(8))
        self.assertEqual(ioff + icount * 2, voff)

    def test_it_round_trips(self):
        rmv, model = self.build()
        blob = rf.save(rmv)
        again = rf.load(blob)
        out = again.lods[0].models[0]
        self.assertTrue(out.indices_first)
        self.assertEqual(out.mesh.vertex_count, model.mesh.vertex_count)
        self.assertEqual(len(out.mesh.indices), len(model.mesh.indices))
        np.testing.assert_array_equal(out.mesh.indices, model.mesh.indices)
        self.assertEqual(rf.save(again), blob)

    def test_a_short_section_size_is_written_back_not_recomputed(self):
        """CA's own files declare a size that stops at the vertex block,
        and recomputing it would change the bytes."""
        rmv, model = self.build()
        blob = rf.save(rmv)
        common = rf._common_header_struct(8)
        start = rf._file_header_struct(8).size + rf._lod_header_struct(8).size
        _, _, sect, voff = common.unpack_from(blob, start)[0:4]
        again = rf.load(blob)
        out = again.lods[0].models[0]
        self.assertEqual(out.declared_section_size, sect)
        out.declared_section_size = voff        # what CA writes
        patched = rf.save(again)
        self.assertEqual(
            common.unpack_from(patched, start)[2], voff)
        # And it survives another trip, vertices intact.
        reread = rf.load(patched)
        self.assertEqual(reread.lods[0].models[0].mesh.vertex_count,
                         model.mesh.vertex_count)
        self.assertEqual(rf.save(reread), patched)

    def test_an_ordinary_section_is_untouched(self):
        rmv, _ = self.build()
        rmv.lods[0].models[0].indices_first = False
        blob = rf.save(rmv)
        common = rf._common_header_struct(8)
        start = rf._file_header_struct(8).size + rf._lod_header_struct(8).size
        _, _, _, voff, _, ioff, _ = common.unpack_from(blob, start)[0:7]
        self.assertLess(voff, ioff)
        self.assertFalse(rf.load(blob).lods[0].models[0].indices_first)


class TestDeclaredFormatId(unittest.TestCase):
    """Six layouts have ids of this module's own making, because the
    file's field does not identify them.  A material built in Blender has
    no file to remember what it declared, so writing it back has to go
    through the id CA actually writes - or the game reads a number it has
    never seen."""

    def test_the_private_ids_map_back(self):
        expected = {rf.VF_SWAY: rf.VF_SWAY_DECLARED,
                    rf.VF_VEGETATION: rf.VF_CUSTOM_TERRAIN,
                    rf.VF_GRASS: rf.VF_POSITION16,
                    rf.VF_POSITION_HALF: rf.VF_POSITION16,
                    rf.VF_TREE_BILLBOARD: rf.VF_ROME2_TREE,
                    rf.VF_POSITION_UV: rf.VF_ROME2_WATER}
        for fmt, declared in expected.items():
            with self.subTest(fmt=rf.VERTEX_FORMAT_NAMES[fmt]):
                self.assertEqual(rf.declared_format_id(fmt), declared)

    def test_ordinary_layouts_declare_themselves(self):
        for fmt in (rf.VF_STATIC, rf.VF_WEIGHTED, rf.VF_CINEMATIC,
                    rf.VF_COLLISION, rf.VF_CUSTOM_TERRAIN2):
            self.assertEqual(rf.declared_format_id(fmt), fmt)

    def test_a_from_scratch_material_writes_the_right_number(self):
        mat = rf.WeightedMaterial(material_id=97, vertex_format=rf.VF_SWAY)
        blob = mat.write(7)
        self.assertEqual(struct.unpack_from("<H", blob, 0)[0],
                         rf.VF_SWAY_DECLARED)

    def test_a_loaded_material_keeps_what_the_file_said(self):
        mat = rf.WeightedMaterial(material_id=97, vertex_format=rf.VF_SWAY)
        mat.declared_vertex_format = 12
        self.assertEqual(struct.unpack_from("<H", mat.write(7), 0)[0], 12)


class TestDecalMaterial(unittest.TestCase):
    """The projected-decal family: a texture path and a short float block
    whose length is the version of the decal."""

    PATH = "rigidmodels/campaign/textures/quarry_underlay"

    def build(self, material_id=87, values=(0.0, 0.0, 0.0, 2.29, 2.29,
                                            2.29, 0.0, 0.0, 0.0)):
        return rf.DecalMaterial(material_id=material_id,
                                texture_path=self.PATH, values=values)

    def test_sizes(self):
        self.assertEqual(self.build(67, (0.0,)).compute_size(6), 260)
        self.assertEqual(self.build().compute_size(6), 292)
        self.assertEqual(self.build(95, (0.0,) * 10).compute_size(6), 296)
        # v5 doubles the path, not the numbers
        self.assertEqual(self.build(67, (0.0,)).compute_size(5), 516)

    def test_roundtrip(self):
        for version in (5, 6):
            with self.subTest(version=version):
                mat = self.build()
                blob = mat.write(version)
                self.assertEqual(len(blob), mat.compute_size(version))
                again = rf.DecalMaterial.parse(blob, 0, 87, len(blob),
                                               version)
                self.assertEqual(again.texture_path, self.PATH)
                np.testing.assert_allclose(again.values, mat.values,
                                           atol=1e-6)
                self.assertEqual(again.write(version), blob)

    def test_the_path_is_reported_as_a_diffuse_texture(self):
        mat = self.build()
        self.assertEqual(mat.textures,
                         [(rf.TEXTURE_TYPE_DIFFUSE, self.PATH)])
        self.assertEqual(mat.get_texture(rf.TEXTURE_TYPE_DIFFUSE), self.PATH)
        self.assertIsNone(mat.get_texture(rf.TEXTURE_TYPE_NORMAL))

    def test_in_a_whole_file(self):
        rmv = make_file(version=6, vertex_format=rf.VF_STATIC, lods=1)
        for model in rmv.lods[0].models:
            model.material = self.build()
            model.material.vertex_format = rf.VF_STATIC
        blob = rf.save(rmv)
        out = rf.load(blob)
        self.assertIsInstance(out.lods[0].models[0].material, rf.DecalMaterial)
        self.assertEqual(out.lods[0].models[0].material.texture_path,
                         self.PATH)
        self.assertEqual(rf.save(out), blob)


class TestTerrainTileMaterial(unittest.TestCase):
    """Warhammer's terrain tile carries six trailing u32; Rome 2 and
    Attila ship one with five and one with six, under ids of their own."""

    def test_five_and_six_word_variants(self):
        for count in (5, 6):
            with self.subTest(words=count):
                mat = rf.TerrainTileMaterial(
                    model_name="TerrainBase0",
                    name_raw=rf._encode_fixed_string("TerrainBase0", 64),
                    unknowns=tuple(range(count)))
                blob = mat.write(6)
                self.assertEqual(len(blob), 64 + 4 * count)
                again = rf.TerrainTileMaterial.parse(blob, 0, len(blob), 6)
                self.assertEqual(again.model_name, "TerrainBase0")
                self.assertEqual(again.unknowns, tuple(range(count)))
                self.assertEqual(again.write(6), blob)

    def test_v5_doubles_the_name(self):
        mat = rf.TerrainTileMaterial(model_name="TerrainBase0",
                                     unknowns=(1, 2, 3, 4, 5))
        blob = mat.write(5)
        self.assertEqual(len(blob), 128 + 20)
        self.assertIn("TerrainBase0".encode("utf-16-le"), blob)
        again = rf.TerrainTileMaterial.parse(blob, 0, len(blob), 5)
        self.assertEqual(again.model_name, "TerrainBase0")
        self.assertEqual(again.unknowns, (1, 2, 3, 4, 5))


class TestEmptyMaterial(unittest.TestCase):
    """Bow waves and one terrain-tile id have no material header at all,
    so the vertex layout has to come from the stride."""

    def test_roundtrip_through_a_file(self):
        rmv = make_file(version=6, vertex_format=rf.VF_STATIC, lods=1)
        for model in rmv.lods[0].models:
            model.material = rf.EmptyMaterial(material_id=22)
            model.material.vertex_format = rf.VF_STATIC
        blob = rf.save(rmv)
        out = rf.load(blob)
        mat = out.lods[0].models[0].material
        self.assertIsInstance(mat, rf.EmptyMaterial)
        self.assertEqual(mat.material_id, 22)
        # resolved from the 32-byte stride, with nothing declaring it
        self.assertEqual(mat.vertex_format, rf.VF_STATIC)
        self.assertEqual(rf.save(out), blob)

    def test_it_writes_nothing(self):
        self.assertEqual(rf.EmptyMaterial().write(6), b"")
        self.assertEqual(rf.EmptyMaterial().compute_size(6), 0)


class TestKeptBlocks(unittest.TestCase):
    """Two blocks are kept exactly as read because nothing here could
    rebuild them: the cloth/rope simulation data inside those materials,
    and the extra indices some sections carry after their index block."""

    def test_cloth_trailing_block(self):
        rmv = make_file(version=6, vertex_format=rf.VF_STATIC, lods=1)
        for model in rmv.lods[0].models:
            model.material.material_id = 60          # cloth
            model.material.trailing = bytes(range(40))
        blob = rf.save(rmv)
        out = rf.load(blob)
        self.assertEqual(out.lods[0].models[0].material.trailing,
                         bytes(range(40)))
        self.assertEqual(rf.save(out), blob)

    def test_a_trailing_block_is_only_allowed_for_those_ids(self):
        """Anywhere else, a material that does not consume its declared
        size is a parse error and stays one."""
        rmv = make_file(version=6, vertex_format=rf.VF_STATIC, lods=1)
        for model in rmv.lods[0].models:
            model.material.material_id = rf.MAT_DEFAULT
            model.material.trailing = bytes(range(40))
        with self.assertRaises(rf.RmvFormatError):
            rf.save(rmv)

    def test_section_tail(self):
        rmv = make_file(version=6, vertex_format=rf.VF_STATIC, lods=1)
        tail = bytes(range(24))
        for model in rmv.lods[0].models:
            model.section_tail = tail
        blob = rf.save(rmv)
        out = rf.load(blob)
        self.assertEqual(out.lods[0].models[0].section_tail, tail)
        self.assertEqual(rf.save(out), blob)

    def test_a_section_tail_is_not_counted_as_vertex_data(self):
        """The LOD header's totals cover the vertex and index blocks, so
        a tail must not push them up - otherwise every file with one
        would come back with declared_sizes set."""
        rmv = make_file(version=6, vertex_format=rf.VF_STATIC, lods=1)
        for model in rmv.lods[0].models:
            model.section_tail = bytes(range(24))
        out = rf.load(rf.save(rmv))
        self.assertIsNone(out.lods[0].declared_sizes)


class TestDeclaredVertexCount(unittest.TestCase):
    """Warhammer 3's decals declare a vertex count and then ship no
    vertex block - the game builds the geometry itself.  Shogun 2's
    non-renderable meshes have always done this; the modern path had to
    learn it too."""

    def make(self):
        """A v8 file whose only mesh has vertices, with the vertex block
        cut out and the count left alone."""
        rmv = make_file(version=8, vertex_format=rf.VF_STATIC, lods=1)
        del rmv.lods[0].models[1:]
        model = rmv.lods[0].models[0]
        count = model.mesh.vertex_count
        blob = bytearray(rf.save(rmv))
        at = 140 + rf._LOD_HEADER_V7_V8.size
        common = rf._COMMON_HEADER.unpack_from(blob, at)
        vertex_offset, index_offset = common[3], common[5]
        block = index_offset - vertex_offset
        del blob[at + vertex_offset:at + index_offset]
        rf._COMMON_HEADER.pack_into(
            blob, at, common[0], common[1], common[2] - block, vertex_offset,
            count, vertex_offset, common[6], *common[7:13],
            common[13], common[14], common[15])
        struct.pack_into("<I", blob, 144, 0)          # no vertex bytes
        return bytes(blob), count

    def test_the_count_survives(self):
        blob, count = self.make()
        out = rf.load(blob)
        model = out.lods[0].models[0]
        self.assertEqual(model.mesh.vertex_count, 0)
        self.assertEqual(model.declared_vertex_count, count)
        self.assertEqual(model.written_vertex_count, count)
        self.assertEqual(rf.save(out), blob)


class TestDeclaredLodSizes(unittest.TestCase):
    """Attila's bow waves declare zero vertex and index bytes whatever
    they hold; the values are kept rather than recomputed."""

    def test_kept(self):
        rmv = make_file(version=6, vertex_format=rf.VF_STATIC, lods=1)
        blob = bytearray(rf.save(rmv))
        struct.pack_into("<II", blob, 144, 0, 0)     # both totals to zero
        out = rf.load(bytes(blob))
        self.assertEqual(out.lods[0].declared_sizes, (0, 0))
        self.assertEqual(rf.save(out), bytes(blob))

    def test_absent_when_they_agree(self):
        out = rf.load(rf.save(make_file(version=6,
                                        vertex_format=rf.VF_STATIC, lods=1)))
        self.assertIsNone(out.lods[0].declared_sizes)


class TestZeroVertexMesh(unittest.TestCase):
    """Rope and light meshes declare a vertex format and ship no
    vertices - including formats that have no layout here."""

    def test_an_unknown_format_with_no_vertices_still_writes(self):
        rmv = make_file(version=6, vertex_format=rf.VF_STATIC, lods=1)
        for model in rmv.lods[0].models:
            model.mesh = rf.RmvMeshData.empty(0, 0)
            model.material.vertex_format = 11        # Attila's ropes
        blob = rf.save(rmv)
        out = rf.load(blob)
        self.assertEqual(out.lods[0].models[0].mesh.vertex_count, 0)
        self.assertEqual(out.lods[0].models[0].material.vertex_format, 11)
        self.assertEqual(rf.save(out), blob)


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

    def test_v4(self):
        self.check(4)

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

    def test_v8_writes_back(self):
        """Version 8 used to be import-only, as it still is in
        AssetEditor."""
        blob = make_anim_v8_bytes()
        anim = af.load(blob)
        self.assertEqual(anim.version, 8)
        self.assertEqual(af.save(anim), blob)

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


class TestAnimHeaderType(unittest.TestCase):
    """The u32 after the version is not the constant AssetEditor takes it
    for.  38 of Warhammer 3's v7 animations (all bird03 attacks) carry 2
    there and one v8 file carries 0; hardcoding 1 lost that."""

    def test_default_is_one(self):
        self.assertEqual(af.AnimFile().header_type, 1)
        blob = af.save(make_anim())
        self.assertEqual(struct.unpack_from("<I", blob, 4)[0], 1)

    def test_written_where_the_version_says(self):
        anim = make_anim()
        anim.header_type = 2
        blob = af.save(anim)
        self.assertEqual(struct.unpack_from("<2I", blob, 0), (7, 2))

    def test_round_trip(self):
        for value in (0, 1, 2):
            with self.subTest(header_type=value):
                anim = make_anim()
                anim.header_type = value
                blob = af.save(anim)
                out = af.load(blob)
                self.assertEqual(out.header_type, value)
                self.assertEqual(af.save(out), blob)

    def test_survives_every_writable_version(self):
        for version in (5, 6, 7):
            with self.subTest(version=version):
                anim = make_anim(version=version)
                anim.header_type = 2
                self.assertEqual(af.load(af.save(anim)).header_type, 2)


class TestAnimQuaternionExtreme(unittest.TestCase):
    """Rotations are int16/32767, so -32768 is representable and reads
    back as -1.00003.  Clamping the float to -1.0 before scaling wrote it
    out as -32767 - one byte per occurrence, in 11 of the Warhammer 3
    files sampled."""

    LOW = -32768.0 / 32767.0

    def rotations_of(self, blob, anim):
        """The int16s the writer actually produced, in order."""
        raw = np.frombuffer(blob, dtype="<i2")
        return raw

    def test_minus_32768_survives(self):
        anim = make_anim(frames=2)
        part = anim.parts[0]
        part.dynamic_frames[0].rotations[0] = [self.LOW, 0.0, 0.0, 0.0]
        blob = af.save(anim)
        out = af.load(blob)
        self.assertEqual(
            int(round(float(out.parts[0].dynamic_frames[0].rotations[0][0])
                      * 32767.0)), -32768)
        self.assertEqual(af.save(out), blob, "and it stays put on re-save")

    def test_out_of_range_still_clamped(self):
        """The clamp has to keep doing its real job: a component over 1.0
        must not wrap round to a large negative int16."""
        anim = make_anim(frames=2)
        anim.parts[0].dynamic_frames[0].rotations[0] = [1.5, -1.5, 1.0, -1.0]
        out = af.load(af.save(anim))
        got = np.round(np.asarray(
            out.parts[0].dynamic_frames[0].rotations[0]) * 32767.0)
        np.testing.assert_array_equal(got, [32767, -32768, 32767, -32767])

    def test_ordinary_values_unchanged(self):
        anim = make_anim(frames=3)
        before = [np.array(f.rotations, np.float32)
                  for f in anim.parts[0].dynamic_frames]
        out = af.load(af.save(anim))
        for want, frame in zip(before, out.parts[0].dynamic_frames):
            np.testing.assert_allclose(frame.rotations, want, atol=1e-6)


class TestAnimStaticOnlyFrameCount(unittest.TestCase):
    """A v7 part can hold a pose in the static frame and have no dynamic
    frames at all.  The frame-count word that follows is usually 3 - but
    in CA's files it is round(duration * fps) + 1, the length the
    animation would have had, and writing 3 over it changed 18 of
    Warhammer 3's animations."""

    def make(self):
        anim = make_anim(version=7, with_static=True)
        anim.parts[0].dynamic_frames = []
        for mapping in (anim.parts[0].translation_mappings
                        + anim.parts[0].rotation_mappings):
            if 0 <= mapping.value < 10000:
                mapping.value = -1
        return anim

    def test_three_when_nothing_says_otherwise(self):
        anim = self.make()
        self.assertIsNone(anim.parts[0].declared_frame_count)
        blob = af.save(anim)
        self.assertEqual(struct.unpack_from("<3i", blob, len(blob) - 12),
                         (0, 0, 3))

    def test_the_files_value_is_kept(self):
        anim = self.make()
        anim.parts[0].declared_frame_count = 81
        blob = af.save(anim)
        self.assertEqual(struct.unpack_from("<3i", blob, len(blob) - 12),
                         (0, 0, 81))
        out = af.load(blob)
        self.assertEqual(out.parts[0].declared_frame_count, 81)
        self.assertEqual(af.save(out), blob)

    def test_only_set_when_there_are_no_dynamic_frames(self):
        """A part with frames has a real count there, not a leftover."""
        anim = make_anim(version=7, with_static=True)
        out = af.load(af.save(anim))
        self.assertIsNone(out.parts[0].declared_frame_count)
        self.assertEqual(len(out.parts[0].dynamic_frames),
                         len(anim.parts[0].dynamic_frames))


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


def make_anim_v8_bytes(ranged=False, parts=1, static_only=False):
    """A version 8 file, written by hand from the format description.

    Two bones: the first animated, the second held in the static frame.
    `ranged` switches both channels from the uncompressed rates (12 and
    8) to the byte-packed ones (3 and 4), which decode through the range
    map.  Every value is chosen to survive the encoding exactly, so a
    round trip can be compared byte for byte.
    """
    out = bytearray()
    out += struct.pack("<IIf", 8, 1, 20.0)
    out += struct.pack("<H", 2) + b"sk"              # skeleton name
    out += struct.pack("<I", 0)                      # flag count
    out += struct.pack("<f", 0.1)                    # duration
    out += struct.pack("<I", 2)                      # bone count
    for name, parent in (("a", -1), ("b", 0)):
        out += struct.pack("<H", 1) + name.encode()
        out += struct.pack("<i", parent)
    out += struct.pack("<I", 6)                      # the word after it
    out += struct.pack("<I", parts)

    trate, rrate = (3, 4) if ranged else (12, 8)
    for _ in range(parts):
        # bone 0 dynamic (positive), bone 1 static (negative)
        first = -trate if static_only else trate
        first_rot = -rrate if static_only else rrate
        out += struct.pack("<bb", first, -trate)
        out += struct.pack("<bb", first_rot, -rrate)

        if ranged:
            out += struct.pack("<II", 2, 2)          # range map lengths
            for _bone in range(2):                   # scale then base
                out += struct.pack("<6f", 2.0, 2.0, 2.0, 0.0, 0.0, 0.0)
            for _bone in range(2):
                out += struct.pack("<8f", 1.0, 1.0, 1.0, 1.0,
                                   0.0, 0.0, 0.0, 0.0)
        else:
            out += struct.pack("<II", 0, 0)

        statics = 2 if static_only else 1
        out += struct.pack("<II", statics, statics)
        for i in range(statics):
            if ranged:
                out += struct.pack("<3b", 127, 0, -127)
                out += struct.pack("<4b", 64, -64, 0, 127)
            else:
                out += struct.pack("<3f", 5.0 + i, 6.0, 7.0)
                out += struct.pack("<4h", 0, 0, 0, 32767)

        if static_only:
            # no dynamic data: the count word is a leftover, not a count
            out += struct.pack("<III", 0, 0, 7)
            continue
        out += struct.pack("<III", 1, 1, 2)
        for f in range(2):
            if ranged:
                out += struct.pack("<3b", 127 - f, 0, -127)
                out += struct.pack("<4b", 127, -127, 0, 64)
            else:
                out += struct.pack("<3f", 1.0 + f, 2.0, 3.0)
                out += struct.pack("<4h", 16384, 0, 0, 16384)
    return bytes(out)


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

    def test_multi_part_concatenates_in_order(self):
        # The game plays parts back to back (AssetEditor's AnimationClip
        # ctor just concatenates every part's frames), not as alternates
        # or layers - resolve_all must reproduce that, not just read
        # part 0. Bone a is dynamic in both parts (different values, so
        # concatenation order is verifiable); bone b is static in both
        # parts but with a *different* static value per part, so the
        # combined static_translation flag must come out False even
        # though it's static within each individual part.
        out = self._header([("a", -1), ("b", 0)])
        out += struct.pack("<I", 2)                  # part count

        out += struct.pack("<bb", 12, -12)            # part 0 trans rates
        out += struct.pack("<bb", 0, 0)               # no rotation data
        out += struct.pack("<II", 0, 0)               # range map lengths
        out += struct.pack("<II", 1, 0)               # static: 1 trans
        out += struct.pack("<3f", 5.0, 6.0, 7.0)      # bone b static
        out += struct.pack("<III", 1, 0, 2)           # dyn gate + frames
        out += struct.pack("<3f", 1.0, 2.0, 3.0)
        out += struct.pack("<3f", 2.0, 2.0, 3.0)

        out += struct.pack("<bb", 12, -12)            # part 1 trans rates
        out += struct.pack("<bb", 0, 0)
        out += struct.pack("<II", 0, 0)
        out += struct.pack("<II", 1, 0)
        out += struct.pack("<3f", 10.0, 11.0, 12.0)   # bone b static (diff)
        out += struct.pack("<III", 1, 0, 1)
        out += struct.pack("<3f", 9.0, 9.0, 9.0)

        anim = af.load(bytes(out))
        self.assertEqual(len(anim.parts), 2)
        self.assertEqual(anim.frame_count, 3)

        res = af.resolve_all(anim)
        self.assertEqual(res.translations.shape, (3, 2, 3))
        np.testing.assert_allclose(
            res.translations[:, 0],
            [[1.0, 2.0, 3.0], [2.0, 2.0, 3.0], [9.0, 9.0, 9.0]], atol=1e-6)
        np.testing.assert_allclose(
            res.translations[:, 1],
            [[5.0, 6.0, 7.0], [5.0, 6.0, 7.0], [10.0, 11.0, 12.0]],
            atol=1e-6)
        self.assertTrue(res.has_translation.all())
        self.assertFalse(res.static_translation[1],
                         "bone b's static value differs between parts")


# ---------------------------------------------------------------------------
# Shogun 2: .anim version 1
# ---------------------------------------------------------------------------

def make_anim_v1(frames=3, events=(), bones=None):
    """Shogun 2 animation: every bone dynamic, float32 quaternions."""
    bones = list(ANIM_BONES if bones is None else bones)
    n = len(bones)
    anim = af.AnimFile(version=1, frame_rate=30.0)
    anim.bones = bones
    anim.duration = (frames - 1) / anim.frame_rate
    anim.events = [tuple(e) for e in events]

    part = af.AnimPart()
    part.translation_mappings = [af.BoneMapping(i) for i in range(n)]
    part.rotation_mappings = [af.BoneMapping(i) for i in range(n)]
    for f in range(frames):
        trans = np.arange(n * 3, dtype=np.float32).reshape(n, 3) + f
        rots = np.zeros((n, 4), np.float32)
        rots[:, 3] = 1.0
        rots[:, 0] = 0.125 * (f + 1)        # exact in float32
        part.dynamic_frames.append(
            af.AnimFrame(translations=trans, rotations=rots))
    anim.parts = [part]
    return anim


class TestAnimV8Writing(unittest.TestCase):
    """Version 8 packs each bone's channels at its own rate, and the
    byte-packed ones decode through a per-bone range.  Writing it means
    reproducing that, not just the values."""

    def load(self, **kwargs):
        return af.load(make_anim_v8_bytes(**kwargs))

    def test_round_trip_is_exact(self):
        for ranged in (False, True):
            with self.subTest(ranged=ranged):
                blob = make_anim_v8_bytes(ranged=ranged)
                self.assertEqual(af.save(af.load(blob)), blob)

    def test_rates_and_ranges_survive(self):
        anim = self.load(ranged=True)
        part = anim.parts[0]
        self.assertIsNotNone(part.translation_rates)
        self.assertIsNotNone(part.rotation_ranges)
        out = af.load(af.save(anim)).parts[0]
        np.testing.assert_array_equal(out.translation_rates,
                                      part.translation_rates)
        np.testing.assert_array_equal(out.rotation_rates,
                                      part.rotation_rates)
        np.testing.assert_allclose(out.translation_ranges,
                                   part.translation_ranges, rtol=1e-6)

    def test_multiple_parts(self):
        """Only version 8 has parts, and it is common: a third of
        Warhammer 3's v8 files carry two or more."""
        anim = self.load(parts=3)
        self.assertEqual(len(anim.parts), 3)
        blob = af.save(anim)
        self.assertEqual(len(af.load(blob).parts), 3)
        self.assertEqual(af.save(af.load(blob)), blob)

    def test_other_versions_still_refuse_parts(self):
        anim = self.load(parts=2)
        anim.version = 7
        with self.assertRaises(af.AnimFormatError):
            af.save(anim)

    def test_built_from_scratch_uses_the_plain_rates(self):
        """Nothing has told a fresh animation which bones to compress,
        so it gets the uncompressed rates - no range map, nothing lost."""
        anim = make_anim(version=8)
        blob = af.save(anim)
        out = af.load(blob)
        part = out.parts[0]
        self.assertTrue(all(int(r) == 12 for r in part.translation_rates))
        self.assertTrue(all(int(r) == 8 for r in part.rotation_rates))
        self.assertEqual(len(part.translation_ranges), 0)
        np.testing.assert_allclose(
            part.dynamic_frames[0].translations,
            anim.parts[0].dynamic_frames[0].translations, atol=1e-6)

    def test_the_word_after_the_bone_table(self):
        """6 in every vanilla v8 file, so that is the default."""
        self.assertEqual(af.AnimFile().unknown_v8, 6)
        blob = af.save(make_anim(version=8))
        self.assertEqual(af.load(blob).unknown_v8, 6)

    def test_a_range_is_refitted_when_it_no_longer_fits(self):
        """Editing a bone past the range it was exported with must move
        the range, not clip the animation to it."""
        anim = self.load(ranged=True)
        part = anim.parts[0]
        moved = part.dynamic_frames[0].translations.copy()
        moved[0] = [50.0, -50.0, 50.0]
        part.dynamic_frames[0].translations = moved
        out = af.load(af.save(anim))
        np.testing.assert_allclose(
            out.parts[0].dynamic_frames[0].translations[0],
            [50.0, -50.0, 50.0], rtol=1e-2)

    def test_static_only_part(self):
        anim = self.load(static_only=True)
        self.assertEqual(anim.parts[0].dynamic_frames, [])
        self.assertIsNotNone(anim.parts[0].static_frame)
        self.assertEqual(af.save(anim), make_anim_v8_bytes(static_only=True))

    def test_frame_count_mismatch_is_caught(self):
        anim = self.load()
        anim.parts[0].dynamic_frames[0].rotations = \
            anim.parts[0].dynamic_frames[0].rotations[:-1]
        with self.assertRaises(af.AnimFormatError):
            af.save(anim)


class TestAnimV4(unittest.TestCase):
    """Rome 2's original .anim layout: UTF-16 strings, two per-bone
    bitfields where v5 keeps its mapping tables, and float32 quaternions
    stored per bone rather than as separate channel blocks."""

    def test_strings_are_utf16(self):
        blob = af.save(make_anim(version=4))
        self.assertIn("humanoid01".encode("utf-16-le"), blob)
        self.assertNotIn(b"humanoid01", blob)
        # a character count, not a byte count
        at = blob.index("humanoid01".encode("utf-16-le")) - 2
        self.assertEqual(struct.unpack_from("<H", blob, at)[0], 10)

    def test_flags_default_to_all_set(self):
        anim = af.load(af.save(make_anim(version=4)))
        part = anim.parts[0]
        self.assertEqual(list(part.translation_flags), [True] * 4)
        self.assertEqual(list(part.rotation_flags), [True] * 4)

    def test_flags_survive_a_roundtrip(self):
        anim = make_anim(version=4)
        anim.parts[0].translation_flags = np.array([True, False, False,
                                                    True])
        anim.parts[0].rotation_flags = np.array([False, True, True, False])
        out = af.load(af.save(anim))
        self.assertEqual(list(out.parts[0].translation_flags),
                         [True, False, False, True])
        self.assertEqual(list(out.parts[0].rotation_flags),
                         [False, True, True, False])
        self.assertEqual(af.save(out), af.save(anim))

    def test_quaternions_are_float32(self):
        """v5 quantizes to int16; v4 does not, so a value that int16
        cannot hold survives here and would not there."""
        anim = make_anim(version=4)
        anim.parts[0].dynamic_frames[0].rotations = np.array(
            [[0.1234567, 0.0, 0.0, 0.9], [0.0, 0.0, 0.0, 1.0],
             [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, 1.0]], np.float32)
        out = af.load(af.save(anim))
        self.assertAlmostEqual(
            float(out.parts[0].dynamic_frames[0].rotations[0][0]),
            0.1234567, places=6)

    def test_static_and_unmapped_bones_are_refused(self):
        """A v4 file stores every bone in every frame, so there is nowhere
        to put a static or unmapped one."""
        for kwargs in ({"with_static": True}, {"with_none": True}):
            with self.subTest(**kwargs):
                anim = make_anim(version=7, **kwargs)
                anim.version = 4
                with self.assertRaises(af.AnimFormatError):
                    af.save(anim)


class TestAnimV8Precision(unittest.TestCase):
    """A version 8 byte decodes as `base + (byte / 127) * scale`.  Where
    the base dwarfs the scale, one byte step is finer than float32
    resolves - Warhammer 3 has a bone whose base is 0.7071 and whose
    scale is 4.2e-06, a step of 3.3e-08 against a float32 grid of
    6e-08 there.  Decoding that to float32 merges adjacent bytes and the
    byte is gone for good, so the frames are float64.
    """

    # the real range from cam_hu1l_theodore_bruckner_bc2_lance_stand_01
    SCALE = 4.20212746e-06
    BASE = 0.707102537

    def make(self, byte):
        out = bytearray()
        out += struct.pack("<IIf", 8, 1, 20.0)
        out += struct.pack("<H", 2) + b"sk"
        out += struct.pack("<I", 0)
        out += struct.pack("<f", 0.1)
        out += struct.pack("<I", 1)
        out += struct.pack("<H", 1) + b"a"
        out += struct.pack("<i", -1)
        out += struct.pack("<I", 6)
        out += struct.pack("<I", 1)                  # one part
        out += struct.pack("<b", 0)                  # no translation
        out += struct.pack("<b", 4)                  # ranged rotation
        out += struct.pack("<II", 0, 1)              # range map lengths
        out += struct.pack("<8f", self.SCALE, self.SCALE, self.SCALE,
                           self.SCALE, self.BASE, self.BASE, self.BASE,
                           self.BASE)
        out += struct.pack("<II", 0, 0)              # no static frame
        out += struct.pack("<III", 0, 1, 1)          # one dynamic frame
        out += struct.pack("<4b", byte, byte, byte, byte)
        return bytes(out)

    def test_the_extreme_byte_survives(self):
        for byte in (127, 126, -127, 1, 0):
            with self.subTest(byte=byte):
                blob = self.make(byte)
                self.assertEqual(af.save(af.load(blob)), blob)

    def test_frames_are_float64(self):
        """float32 would merge 126 and 127 here - the whole point."""
        anim = af.load(self.make(127))
        self.assertEqual(anim.parts[0].dynamic_frames[0].rotations.dtype,
                         np.float64)

    def test_float32_really_would_lose_it(self):
        """The premise, asserted rather than assumed: at this range the
        two bytes land on the same float32."""
        at = lambda b: np.float32(self.BASE + (b / 127.0) * self.SCALE)
        self.assertEqual(at(127), at(126))
        self.assertNotEqual(self.BASE + (127 / 127.0) * self.SCALE,
                            self.BASE + (126 / 127.0) * self.SCALE)

    def test_other_versions_stay_float32(self):
        """Only v8 needs the wider frames; v5-v7 quantize to int16, which
        float32 holds exactly."""
        anim = af.load(af.save(make_anim(version=7)))
        self.assertEqual(anim.parts[0].dynamic_frames[0].rotations.dtype,
                         np.float32)


class TestAnimShogun2(unittest.TestCase):
    def test_roundtrip(self):
        anim = make_anim_v1(frames=4)
        blob = af.save(anim)
        out = af.load(blob)

        self.assertEqual(out.version, 1)
        self.assertEqual(out.frame_rate, 30.0)
        self.assertAlmostEqual(out.duration, 3 / 30.0, places=6)
        self.assertEqual([(b.name, b.parent) for b in out.bones],
                         [(b.name, b.parent) for b in anim.bones])
        self.assertEqual(len(out.parts[0].dynamic_frames), 4)
        for f_in, f_out in zip(anim.parts[0].dynamic_frames,
                               out.parts[0].dynamic_frames):
            np.testing.assert_array_equal(f_out.translations,
                                          f_in.translations)
            np.testing.assert_array_equal(f_out.rotations, f_in.rotations)
        self.assertEqual(af.save(out), blob)

    def test_quaternions_are_full_float32(self):
        """v1 stores quaternions as float32, so unlike v5+ they must not
        be quantized to 1/32767."""
        anim = make_anim_v1(frames=1)
        anim.parts[0].dynamic_frames[0].rotations[0] = [1e-6, 0, 0, 1]
        out = af.load(af.save(anim))
        self.assertEqual(out.parts[0].dynamic_frames[0].rotations[0][0],
                         np.float32(1e-6))

    def test_events(self):
        events = [("FIRE_TIME", "0.46"),
                  ("FIRE_POSITION", "0.00", "3.66", "0.24")]
        out = af.load(af.save(make_anim_v1(events=events)))
        self.assertEqual(out.events, [tuple(e) for e in events])

    def test_utf16_bone_names(self):
        """Names are UTF-16 with a character count, so non-ASCII names
        must survive and must not be measured in bytes."""
        bones = [af.AnimBone("台場", -1), af.AnimBone("bone_1", 0)]
        out = af.load(af.save(make_anim_v1(bones=bones)))
        self.assertEqual([b.name for b in out.bones],
                         ["台場", "bone_1"])

    def test_non_dynamic_mapping_rejected(self):
        anim = make_anim_v1()
        anim.parts[0].rotation_mappings[1] = af.BoneMapping(af.MAPPING_NONE)
        with self.assertRaises(af.AnimFormatError):
            af.save(anim)

    def test_static_frame_rejected(self):
        anim = make_anim_v1()
        anim.parts[0].static_frame = af.AnimFrame()
        with self.assertRaises(af.AnimFormatError):
            af.save(anim)

    def test_trailing_bytes_rejected(self):
        # Too short to be another event block, so it is a real error.
        with self.assertRaises(af.AnimFormatError):
            af.load(af.save(make_anim_v1()) + b"\0\0")

    def test_extra_event_block(self):
        """CA's cinematic test exports carry a second, empty event block
        after the first; it must survive a load/save cycle."""
        blob = af.save(make_anim_v1()) + struct.pack("<I", 0)
        out = af.load(blob)
        self.assertEqual(out.extra_event_blocks, [[]])
        self.assertEqual(af.save(out), blob)

    def test_bone_less_stub_keeps_frame_count(self):
        """A file with no bones still declares a frame count."""
        anim = make_anim_v1(frames=0, bones=[])
        anim.parts[0].dynamic_frames = [af.AnimFrame() for _ in range(101)]
        out = af.load(af.save(anim))
        self.assertEqual(len(out.parts[0].dynamic_frames), 101)


def make_anim_v0(frames=3):
    """The headerless Shogun 2 / Empire layout, as bytes.

    This is what a .rigid_model_animation carries after its objects, so
    the tests for that format build their tail with it.
    """
    anim = make_anim_v1(frames=frames)
    anim.version = af.SHOGUN2_NO_HEADER_VERSION
    for frame in anim.parts[0].dynamic_frames:
        frame.extras = np.full((len(anim.bones), 3), 0.001, np.float32)
    return af.save(anim)


class TestAnimShogun2NoHeader(unittest.TestCase):
    """The headerless variant: no version field, ten floats per bone."""

    def make(self, frames=3):
        anim = make_anim_v1(frames=frames)
        anim.version = af.SHOGUN2_NO_HEADER_VERSION
        for frame in anim.parts[0].dynamic_frames:
            frame.extras = np.full((len(anim.bones), 3), 0.001, np.float32)
        return anim

    def test_roundtrip(self):
        anim = self.make()
        blob = af.save(anim)
        # No version field: the file starts with the frame rate.
        self.assertEqual(struct.unpack_from("<f", blob, 0)[0], 30.0)
        out = af.load(blob)
        self.assertEqual(out.version, af.SHOGUN2_NO_HEADER_VERSION)
        self.assertEqual(out.frame_rate, 30.0)
        for f_in, f_out in zip(anim.parts[0].dynamic_frames,
                               out.parts[0].dynamic_frames):
            np.testing.assert_array_equal(f_out.translations,
                                          f_in.translations)
            np.testing.assert_array_equal(f_out.rotations, f_in.rotations)
            np.testing.assert_array_equal(f_out.extras, f_in.extras)
        self.assertEqual(af.save(out), blob)

    def test_extras_default_to_ca_value(self):
        anim = self.make()
        for frame in anim.parts[0].dynamic_frames:
            frame.extras = np.zeros((0, 3), np.float32)
        out = af.load(af.save(anim))
        np.testing.assert_allclose(
            out.parts[0].dynamic_frames[0].extras, 0.001, atol=1e-9)

    def test_not_confused_with_versioned_files(self):
        """A real v1 file starts with 1, which is not a plausible frame
        rate, so version detection must not misfire in either direction."""
        self.assertEqual(af.load(af.save(make_anim_v1())).version, 1)
        self.assertEqual(af.load(af.save(self.make())).version,
                         af.SHOGUN2_NO_HEADER_VERSION)

    def test_garbage_still_rejected(self):
        blob = af.save(self.make())
        with self.assertRaises(af.AnimFormatError):
            af.load(struct.pack("<I", 0xDEADBEEF) + blob[4:])

    def test_resolve(self):
        anim = make_anim_v1(frames=3)
        res = af.resolve_all(anim)
        self.assertEqual(res.translations.shape, (3, len(ANIM_BONES), 3))
        self.assertTrue(res.has_translation.all())
        self.assertTrue(res.has_rotation.all())


# ---------------------------------------------------------------------------
# Shogun 2: .rigid_model_v2 versions 1 and 2
# ---------------------------------------------------------------------------

def make_s2_material(version, textures=2, bone_index=1, model_name="hull",
                     shader_name="rigid_default"):
    """Build the fixed-width block a Shogun 2 material is, then parse it -
    so the test exercises the real byte layout rather than the dataclass.
    """
    blob = bytearray()
    if version >= 2:            # v1 has no shader name; v2 and v3 do
        blob += rf._encode_fixed_utf16(shader_name, rf._S2_STR32)
    blob += rf._encode_fixed_utf16(model_name, rf._S2_STR32)
    for i in range(textures):
        blob += rf._encode_fixed_utf16(
            f"rigidmodels/naval/textures/tex_{i}" if i == 0 else "",
            rf._S2_STR256)
    if bone_index is not None:
        blob += struct.pack("<i", bone_index)
    return rf.Shogun2Material.parse(bytes(blob), 0, 21, version, len(blob))


def make_s2_file(version=2, lods=2, textures=2, bone_index=1):
    rmv = rf.RmvFile(version=version, skeleton_name="turtle_ship.anim")
    for i in range(lods):
        lod = rf.RmvLod(camera_distance=(i + 1) * 100.0, lod_level=i)
        for _ in range(2):
            mat = make_s2_material(version, textures, bone_index)
            mat.vertex_format = rf.VF_STATIC
            lod.models.append(rf.RmvModel(
                material=mat, mesh=make_cube_mesh(0), render_flag=0))
        rmv.lods.append(lod)
    return rmv


class TestShogun2File(unittest.TestCase):
    def check(self, version, **kwargs):
        rmv = make_s2_file(version=version, **kwargs)
        blob = rf.save(rmv)
        out = rf.load(blob)

        self.assertEqual(out.version, version)
        self.assertTrue(out.is_shogun2)
        self.assertEqual(out.skeleton_name, "turtle_ship.anim")
        self.assertEqual(len(out.lods), len(rmv.lods))
        for lod_in, lod_out in zip(rmv.lods, out.lods):
            self.assertAlmostEqual(lod_out.camera_distance,
                                   lod_in.camera_distance, places=4)
            for m_in, m_out in zip(lod_in.models, lod_out.models):
                mat_in, mat_out = m_in.material, m_out.material
                self.assertEqual(mat_out.material_id, mat_in.material_id)
                self.assertEqual(mat_out.model_name, mat_in.model_name)
                self.assertEqual(mat_out.shader_name, mat_in.shader_name)
                self.assertEqual(mat_out.texture_paths, mat_in.texture_paths)
                self.assertEqual(mat_out.bone_index, mat_in.bone_index)
                np.testing.assert_array_equal(m_out.mesh.indices,
                                              m_in.mesh.indices)
                np.testing.assert_allclose(m_out.mesh.positions,
                                           m_in.mesh.positions, atol=2e-3)

        # Saving what we loaded must be byte-identical (stable roundtrip).
        self.assertEqual(rf.save(out), blob)

    def test_v1(self):
        self.check(1)

    def test_v2(self):
        self.check(2)

    def test_unattached_mesh(self):
        self.check(2, bone_index=-1)

    def test_no_bone_field(self):
        """Material ids such as 32 have no trailing bone index at all."""
        self.check(2, bone_index=None)
        mat = make_s2_material(2, bone_index=None)
        self.assertIsNone(mat.bone_index)
        self.assertEqual(mat.matrix_index, -1)

    def test_header_layout(self):
        """The skeleton name is a 512-byte UTF-16 field sitting between the
        lod table and the first mesh."""
        blob = rf.save(make_s2_file(version=2, lods=3))
        magic, version, lod_count = rf._S2_FILE_HEADER.unpack_from(blob, 0)
        self.assertEqual((magic, version, lod_count), (b"RMV2", 2, 3))
        name_start = (rf._S2_FILE_HEADER.size
                      + rf._LOD_HEADER_V5_V6.size * lod_count + 4)
        first_mesh = rf._LOD_HEADER_V5_V6.unpack_from(
            blob, rf._S2_FILE_HEADER.size)[3]
        self.assertEqual(name_start + rf._S2_NAME_SIZE, first_mesh)
        self.assertEqual(
            rf._decode_fixed_utf16(
                blob[name_start:name_start + rf._S2_NAME_SIZE]),
            "turtle_ship.anim")

    def test_texture_types(self):
        mat = make_s2_material(2, textures=2)
        mat.texture_paths[1] = "rigidmodels/naval/textures/ship_ao"
        self.assertEqual(mat.textures, [
            (rf.TEXTURE_TYPE_DIFFUSE, "rigidmodels/naval/textures/tex_0"),
            (rf.TEXTURE_TYPE_AMBIENT_OCCLUSION,
             "rigidmodels/naval/textures/ship_ao"),
        ])
        # Empty slots are not reported as textures.
        mat.texture_paths[1] = ""
        self.assertEqual(len(mat.textures), 1)

    def test_unknown_material_block_survives(self):
        """An unrecognised trailing field must be preserved verbatim so a
        material id we have never seen still re-saves byte-identically."""
        base = make_s2_material(2, textures=2, bone_index=7)
        raw = base.raw + b"\x11\x22\x33\x44\x55\x66\x77\x88"
        mat = rf.Shogun2Material.parse(raw, 0, 54, 2, len(raw))
        self.assertEqual(mat.bone_index, 7)
        self.assertEqual(mat.write(), raw)

    def test_modern_material_rejected(self):
        """A Rome 2+ material cannot be written into a Shogun 2 file."""
        rmv = make_s2_file(version=2)
        rmv.lods[0].models[0].material = make_material(rf.VF_STATIC)
        with self.assertRaises(rf.RmvFormatError):
            rf.save(rmv)

    def test_declared_sizes_preserved(self):
        """Some CA materials ship zeroed vertex/index totals even though
        real geometry follows; those must survive a load/save cycle."""
        blob = bytearray(rf.save(make_s2_file(version=2, lods=1)))
        struct.pack_into("<II", blob, rf._S2_FILE_HEADER.size + 4, 0, 0)
        out = rf.load(bytes(blob))
        self.assertEqual(out.lods[0].declared_sizes, (0, 0))
        self.assertEqual(rf.save(out), bytes(blob))


# ---------------------------------------------------------------------------
# Shogun 2: .animatable_rigid_model
# ---------------------------------------------------------------------------

def make_arm_mesh(version=5, bone_index=2, vertices=6):
    floats, vec4s = armf.default_params()
    mesh = armf.ArmMesh(version=version, bone_index=bone_index,
                        float_params=floats, vec4_params=vec4s)
    mesh.textures = ["12_pounder_diffuse", "12_pounder_normal",
                     "12_pounder_gloss_map", ""]
    n = vertices
    step = np.arange(n, dtype=np.float32)
    mesh.positions = np.stack([step, step * 2, step * 3], 1)
    mesh.normals = np.tile(np.array([0, 0, 1], np.float32), (n, 1))
    mesh.tangents = np.tile(np.array([1, 0, 0], np.float32), (n, 1))
    mesh.binormals = np.tile(np.array([0, 1, 0], np.float32), (n, 1))
    mesh.uv0 = np.stack([step / n, 1 - step / n], 1)
    mesh.uv1 = np.zeros((n, 2), np.float32)
    mesh.colours = np.ones((n, 4), np.float32)
    mesh.indices = np.arange(n, dtype=np.uint32)
    return mesh


class TestArmRoundtrip(unittest.TestCase):
    def check(self, versions=(5,), objects=2):
        arm = armf.ArmFile(meshes=[
            make_arm_mesh(version=versions[i % len(versions)],
                          bone_index=i, vertices=3 * (i + 2))
            for i in range(objects)])
        blob = armf.save(arm)
        out = armf.load(blob)

        self.assertEqual(len(out.meshes), objects)
        for m_in, m_out in zip(arm.meshes, out.meshes):
            self.assertEqual(m_out.version, m_in.version)
            self.assertEqual(m_out.textures, m_in.textures)
            self.assertEqual(m_out.bone_index, m_in.bone_index)
            # Parameter values are stored as float32, so compare loosely.
            self.assertEqual([n for n, _ in m_out.float_params],
                             [n for n, _ in m_in.float_params])
            np.testing.assert_allclose(
                [v for _, v in m_out.float_params],
                [v for _, v in m_in.float_params], rtol=1e-6)
            self.assertEqual([n for n, _ in m_out.vec4_params],
                             [n for n, _ in m_in.vec4_params])
            np.testing.assert_allclose(
                [v for _, v in m_out.vec4_params],
                [v for _, v in m_in.vec4_params], rtol=1e-6)
            for name in ("positions", "normals", "uv0", "uv1", "tangents",
                         "binormals", "colours", "indices"):
                np.testing.assert_array_equal(
                    getattr(m_out, name), getattr(m_in, name),
                    err_msg=f"{name} mismatch")
        # Saving what we loaded must be byte-identical (stable roundtrip).
        self.assertEqual(armf.save(out), blob)

    def test_single_object(self):
        self.check(objects=1)

    def test_multiple_objects(self):
        self.check(objects=4)

    def test_version_4(self):
        self.check(versions=(4,))

    def test_mixed_versions(self):
        self.check(versions=(4, 5), objects=4)

    def test_layout(self):
        """Spot-check the byte layout: object count, magic, and the pad
        byte that precedes only the first three texture slots."""
        blob = armf.save(armf.ArmFile(meshes=[make_arm_mesh()]))
        count, magic, version = struct.unpack_from("<III", blob, 0)
        self.assertEqual((count, magic, version), (1, armf.ARM_MAGIC, 5))
        pos = 12
        for slot in range(4):
            if slot < 3:
                self.assertEqual(blob[pos], 0, f"pad before slot {slot}")
                pos += 1
            n = struct.unpack_from("<H", blob, pos)[0]
            pos += 2
            text = blob[pos:pos + n * 2].decode("utf-16-le")
            pos += n * 2
            self.assertEqual(text, make_arm_mesh().textures[slot])

    def test_vertex_stride(self):
        mesh = make_arm_mesh(vertices=4)
        blob = armf.save(armf.ArmFile(meshes=[mesh]))
        # Locate the vertex block by re-parsing, then check the stride.
        out = armf.load(blob)
        self.assertEqual(armf.VERTEX_FLOATS, 20)
        np.testing.assert_array_equal(out.meshes[0].positions,
                                      mesh.positions)

    def test_bad_magic(self):
        blob = bytearray(armf.save(armf.ArmFile(meshes=[make_arm_mesh()])))
        struct.pack_into("<I", blob, 4, 0xDEADBEEF)
        with self.assertRaises(armf.ArmFormatError):
            armf.load(bytes(blob))

    def test_bad_version(self):
        mesh = make_arm_mesh()
        mesh.version = 9
        with self.assertRaises(armf.ArmFormatError):
            armf.save(armf.ArmFile(meshes=[mesh]))

    def test_trailing_bytes_rejected(self):
        blob = armf.save(armf.ArmFile(meshes=[make_arm_mesh()]))
        with self.assertRaises(armf.ArmFormatError):
            armf.load(blob + b"\0\0\0\0")

    def test_truncated(self):
        blob = armf.save(armf.ArmFile(meshes=[make_arm_mesh()]))
        with self.assertRaises(armf.ArmFormatError):
            armf.load(blob[:len(blob) // 2])

    def test_out_of_range_index_rejected(self):
        mesh = make_arm_mesh(vertices=4)
        mesh.indices = np.array([0, 1, 99], np.uint32)
        with self.assertRaises(armf.ArmFormatError):
            armf.save(armf.ArmFile(meshes=[mesh]))

    def test_channel_length_mismatch_rejected(self):
        mesh = make_arm_mesh(vertices=4)
        mesh.normals = np.zeros((3, 3), np.float32)
        with self.assertRaises(armf.ArmFormatError):
            armf.save(armf.ArmFile(meshes=[mesh]))

    def test_texture_accessors(self):
        mesh = make_arm_mesh()
        self.assertEqual(mesh.get_texture("diffuse"), "12_pounder_diffuse")
        self.assertEqual(mesh.get_texture("ao"), "")
        mesh.set_texture("ao", "shadow")
        self.assertEqual(mesh.textures[3], "shadow")
        self.assertEqual(mesh.get_float_param("bumpfactor"), 1.0)
        self.assertIsNone(mesh.get_float_param("nope"))

    def test_empty_file(self):
        blob = armf.save(armf.ArmFile())
        self.assertEqual(blob, struct.pack("<I", 0))
        self.assertEqual(armf.load(blob).meshes, [])


class TestRigidModelVariant(unittest.TestCase):
    """Plain .rigid_model: the same container without the per-object bone
    index, closing with a bounding box instead."""

    def make(self, objects=2, version=5):
        arm = armf.ArmFile(meshes=[
            make_arm_mesh(version=version, bone_index=None,
                          vertices=3 * (i + 2))
            for i in range(objects)])
        arm.bounding_box = arm.computed_bounding_box()
        return arm

    def check(self, objects=2, version=5):
        arm = self.make(objects, version)
        blob = armf.save(arm)
        out = armf.load(blob)
        self.assertEqual(len(out.meshes), objects)
        for mesh in out.meshes:
            self.assertIsNone(mesh.bone_index,
                              "a .rigid_model object has no bone index")
        np.testing.assert_allclose(out.bounding_box, arm.bounding_box,
                                   rtol=1e-6)
        self.assertEqual(armf.save(out), blob)
        return blob

    def test_roundtrip(self):
        self.check()

    def test_single_object(self):
        self.check(objects=1)

    def test_version_3_has_no_parameter_block(self):
        """Versions below 4 go straight from textures to geometry."""
        arm = self.make(objects=1, version=3)
        arm.meshes[0].float_params = []
        arm.meshes[0].vec4_params = []
        out = armf.load(armf.save(arm))
        self.assertEqual(out.meshes[0].float_params, [])
        self.assertEqual(out.meshes[0].vec4_params, [])

    def test_version_3_rejects_parameters(self):
        arm = self.make(objects=1, version=3)
        arm.meshes[0].float_params = [("bumpfactor", 1.0)]
        with self.assertRaises(armf.ArmFormatError):
            armf.save(arm)

    def test_texture_flags_preserved(self):
        """.rigid_model uses non-zero flag bytes before the texture names
        where .animatable_rigid_model always uses 0."""
        arm = self.make(objects=1)
        arm.meshes[0].texture_flags = [0, 1, 1]
        out = armf.load(armf.save(arm))
        self.assertEqual(out.meshes[0].texture_flags, [0, 1, 1])

    def test_bone_index_distinguishes_the_two_formats(self):
        """The reader decides per object by lookahead, not by extension."""
        plain = armf.load(armf.save(self.make(objects=2)))
        self.assertTrue(all(m.bone_index is None for m in plain.meshes))

        animatable = armf.ArmFile(meshes=[
            make_arm_mesh(bone_index=i + 1, vertices=6) for i in range(2)])
        loaded = armf.load(armf.save(animatable))
        self.assertEqual([m.bone_index for m in loaded.meshes], [1, 2])
        self.assertIsNone(loaded.bounding_box)


def make_arm_mesh_v1(bone_index=None, vertices=6, texture="mast"):
    """A version 1 object: one unflagged texture name, no parameter
    block, and a vertex two floats shorter (no second UV set)."""
    mesh = make_arm_mesh(version=1, bone_index=bone_index,
                         vertices=vertices)
    mesh.textures = [texture, "", "", ""]
    mesh.texture_flags = [0, 0, 0]
    mesh.float_params = []
    mesh.vec4_params = []
    mesh.uv1 = np.zeros((vertices, 2), np.float32)
    return mesh


class TestArmVersion1(unittest.TestCase):
    """Object version 1, the Empire/Napoleon-era layout: a single
    unflagged texture name and an 18-float vertex."""

    def test_stride_constants(self):
        self.assertEqual(armf.vertex_floats(1), 18)
        self.assertEqual(armf.vertex_floats(5), 20)
        self.assertEqual(armf.V1_VERTEX_FLOATS, armf.VERTEX_FLOATS - 2)
        self.assertIn(1, armf.SUPPORTED_VERSIONS)

    def test_rigid_model_roundtrip(self):
        arm = armf.ArmFile(meshes=[
            make_arm_mesh_v1(bone_index=None, vertices=3 * (i + 2))
            for i in range(2)])
        arm.bounding_box = arm.computed_bounding_box()
        blob = armf.save(arm)
        out = armf.load(blob)
        self.assertEqual(len(out.meshes), 2)
        for mesh in out.meshes:
            self.assertEqual(mesh.version, 1)
            self.assertIsNone(mesh.bone_index)
        self.assertEqual(armf.save(out), blob)

    def test_animatable_roundtrip_keeps_bone_indices(self):
        """The one vanilla v1 .animatable_rigid_model carries bone
        indices exactly as the later versions do."""
        arm = armf.ArmFile(meshes=[
            make_arm_mesh_v1(bone_index=i + 1, vertices=6)
            for i in range(3)])
        out = armf.load(armf.save(arm))
        self.assertEqual([m.bone_index for m in out.meshes], [1, 2, 3])
        self.assertIsNone(out.bounding_box)
        self.assertEqual(armf.save(out), armf.save(arm))

    def test_file_size_is_exactly_the_v1_layout(self):
        """18 floats per vertex, one unflagged name, no parameter block.
        Pinning the whole size catches any stray byte in either."""
        mesh = make_arm_mesh_v1(bone_index=1, vertices=10,
                                texture="mast")
        blob = armf.save(armf.ArmFile(meshes=[mesh]))
        expected = (4                      # object count
                    + 4 + 4                # magic, version
                    + 2 + len("mast") * 2  # the single texture name
                    + 4 + 10 * 18 * 4      # vertex count, vertex block
                    + 4 + len(mesh.indices) * 4   # index count, indices
                    + 4)                   # bone index
        self.assertEqual(len(blob), expected)

    def test_geometry_survives(self):
        source = make_arm_mesh_v1(bone_index=1, vertices=8)
        out = armf.load(armf.save(armf.ArmFile(meshes=[source])))
        result = out.meshes[0]
        for field in ("positions", "normals", "uv0", "tangents",
                      "binormals", "colours"):
            np.testing.assert_allclose(getattr(result, field),
                                       getattr(source, field), rtol=1e-6)
        np.testing.assert_array_equal(result.indices, source.indices)

    def test_second_uv_set_reads_back_as_zeros(self):
        """v1 has no uv1 in the file, but every ArmMesh must still have
        one so the rest of the add-on can treat them alike."""
        out = armf.load(armf.save(
            armf.ArmFile(meshes=[make_arm_mesh_v1(bone_index=1,
                                                  vertices=7)])))
        self.assertEqual(out.meshes[0].uv1.shape, (7, 2))
        self.assertFalse(out.meshes[0].uv1.any())

    def test_texture_name_has_no_flag_byte(self):
        """v3+ put a flag byte before the first three names; v1 does
        not, and the name lands straight after the version field."""
        arm = armf.ArmFile(meshes=[make_arm_mesh_v1(bone_index=1,
                                                    texture="mast")])
        blob = armf.save(arm)
        # object count, magic, version, then the u16 character count.
        self.assertEqual(struct.unpack_from("<H", blob, 12)[0], 4)
        self.assertEqual(blob[14:22].decode("utf-16-le"), "mast")

    def test_extra_texture_slots_refused(self):
        """v1 has nowhere to put normal/gloss/ao, so silently dropping
        them on export would lose data."""
        mesh = make_arm_mesh_v1(bone_index=1)
        mesh.textures = ["mast", "mast_normal", "", ""]
        with self.assertRaises(armf.ArmFormatError):
            armf.save(armf.ArmFile(meshes=[mesh]))

    def test_parameters_refused(self):
        """Parameter blocks only exist from version 4."""
        mesh = make_arm_mesh_v1(bone_index=1)
        mesh.float_params = [("bumpfactor", 1.0)]
        with self.assertRaises(armf.ArmFormatError):
            armf.save(armf.ArmFile(meshes=[mesh]))

    def test_version_2_shares_the_vertex_but_not_the_slots(self):
        """v1 and v2 have the same 18-float vertex; what differs is that
        v2 carries three flagged texture names instead of one bare one."""
        self.assertEqual(armf.vertex_floats(2), armf.vertex_floats(1))
        self.assertEqual(armf.texture_layout(1), (1, 0))
        self.assertEqual(armf.texture_layout(2), (3, 3))

class TestArmVersion2(unittest.TestCase):
    """Object version 2: three flagged texture names (no ao slot) and
    version 1's 18-float vertex.  Only CA's testdata/fence files use it,
    but it completes the ladder - each version adds exactly one thing."""

    def make(self, bone_index=None, vertices=6, objects=1):
        meshes = []
        for i in range(objects):
            mesh = make_arm_mesh(version=2, vertices=vertices,
                                 bone_index=None if bone_index is None
                                 else bone_index + i)
            mesh.textures = ["metal_diffuse", "metal_normal",
                             "metal_gloss_map", ""]
            mesh.texture_flags = [0, 0, 0]
            mesh.float_params = []
            mesh.vec4_params = []
            mesh.uv1 = np.zeros((vertices, 2), np.float32)
            meshes.append(mesh)
        return armf.ArmFile(meshes=meshes)

    def test_layout_constants(self):
        self.assertEqual(armf.texture_layout(2), (3, 3))
        self.assertEqual(armf.vertex_floats(2), 18)
        self.assertIn(2, armf.SUPPORTED_VERSIONS)

    def test_roundtrip_with_bounding_box(self):
        """The shape of all three vanilla files: one object, no bone
        index, closing with a bounding box."""
        arm = self.make()
        arm.bounding_box = arm.computed_bounding_box()
        blob = armf.save(arm)
        out = armf.load(blob)
        self.assertEqual(out.meshes[0].version, 2)
        self.assertIsNone(out.meshes[0].bone_index)
        np.testing.assert_allclose(out.bounding_box, arm.bounding_box,
                                   rtol=1e-6)
        self.assertEqual(armf.save(out), blob)

    def test_three_texture_names_survive(self):
        out = armf.load(armf.save(self.make()))
        self.assertEqual(out.meshes[0].textures[:3],
                         ["metal_diffuse", "metal_normal",
                          "metal_gloss_map"])
        self.assertEqual(out.meshes[0].get_texture("ao"), "",
                         "v2 has no ao slot to read one from")

    def test_ao_slot_refused(self):
        """There is nowhere to put a fourth name, so dropping it silently
        on export would lose data."""
        arm = self.make()
        arm.meshes[0].set_texture("ao", "metal_ao")
        with self.assertRaises(armf.ArmFormatError):
            armf.save(arm)

    def test_texture_flags_are_written(self):
        """Unlike version 1, v2's names each carry a leading flag byte."""
        arm = self.make()
        arm.meshes[0].texture_flags = [0, 1, 4]
        out = armf.load(armf.save(arm))
        self.assertEqual(out.meshes[0].texture_flags, [0, 1, 4])

    def test_file_size_is_exactly_the_v2_layout(self):
        mesh = self.make(bone_index=1, vertices=10).meshes[0]
        blob = armf.save(armf.ArmFile(meshes=[mesh]))
        names = "metal_diffuse", "metal_normal", "metal_gloss_map"
        expected = (4                              # object count
                    + 4 + 4                        # magic, version
                    + sum(1 + 2 + len(n) * 2 for n in names)
                    + 4 + 10 * 18 * 4              # vertices
                    + 4 + len(mesh.indices) * 4    # indices
                    + 4)                           # bone index
        self.assertEqual(len(blob), expected)

    def test_no_parameter_block(self):
        """Parameter blocks start at version 4."""
        arm = self.make()
        arm.meshes[0].float_params = [("bumpfactor", 1.0)]
        with self.assertRaises(armf.ArmFormatError):
            armf.save(arm)

    def test_geometry_survives(self):
        source = self.make(bone_index=1, vertices=8).meshes[0]
        out = armf.load(armf.save(armf.ArmFile(meshes=[source])))
        result = out.meshes[0]
        for field in ("positions", "normals", "uv0", "tangents",
                      "binormals", "colours"):
            np.testing.assert_allclose(getattr(result, field),
                                       getattr(source, field), rtol=1e-6)
        self.assertFalse(result.uv1.any(), "v2 stores no second UV set")


class TestArmVersionLadder(unittest.TestCase):
    """Each object version adds exactly one thing to the one before, and
    every channel keeps its offset - which is why one reader covers the
    lot.  Pinning the table is what keeps that true."""

    def test_the_ladder(self):
        expected = {
            armf.NO_HEADER_VERSION: ((1, 0), 14, False),
            1: ((1, 0), 18, False),
            2: ((3, 3), 18, False),
            3: ((4, 3), 20, False),
            4: ((4, 3), 20, True),
            5: ((4, 3), 20, True),
        }
        for version, (slots, floats, params) in expected.items():
            self.assertEqual(armf.texture_layout(version), slots,
                             f"version {version} texture slots")
            self.assertEqual(armf.vertex_floats(version), floats,
                             f"version {version} vertex stride")
            self.assertEqual(version >= armf._PARAMS_FROM_VERSION, params,
                             f"version {version} parameter block")

    def test_strides_only_ever_shrink(self):
        ladder = [armf.NO_HEADER_VERSION, 1, 2, 3, 4, 5]
        strides = [armf.vertex_floats(v) for v in ladder]
        self.assertEqual(strides, sorted(strides),
                         "a later version never has a shorter vertex")
        slots = [armf.texture_layout(v)[0] for v in ladder]
        self.assertEqual(slots, sorted(slots),
                         "a later version never has fewer texture slots")

# Named material parameter blocks only exist from object version 4.
_PARAMS_FROM_VERSION_TEST = 4


class TestArmHeaderless(unittest.TestCase):
    """The headerless object: no per-object magic or version, one
    unflagged name, and a 14-float vertex (no colour, no second UV)."""

    def make(self, bone_index=1, vertices=6, objects=1):
        meshes = []
        for i in range(objects):
            mesh = make_arm_mesh_v1(
                bone_index=None if bone_index is None else bone_index + i,
                vertices=vertices)
            mesh.version = armf.NO_HEADER_VERSION
            mesh.colours = np.zeros((vertices, 4), np.float32)
            meshes.append(mesh)
        return armf.ArmFile(meshes=meshes)

    def test_stride(self):
        self.assertEqual(armf.vertex_floats(armf.NO_HEADER_VERSION), 14)
        self.assertEqual(armf.V0_VERTEX_FLOATS, armf.V1_VERTEX_FLOATS - 4)

    def test_no_magic_is_written(self):
        blob = armf.save(self.make())
        self.assertEqual(struct.unpack_from("<I", blob, 0)[0], 1)
        # A normal file has the magic at offset 4; this one has the name.
        self.assertNotEqual(struct.unpack_from("<I", blob, 4)[0],
                            armf.ARM_MAGIC)
        self.assertEqual(struct.unpack_from("<H", blob, 4)[0], 4)
        self.assertEqual(blob[6:14].decode("utf-16-le"), "mast")

    def test_roundtrip_detects_itself(self):
        arm = self.make(objects=3)
        blob = armf.save(arm)
        out = armf.load(blob)
        self.assertEqual(len(out.meshes), 3)
        for mesh in out.meshes:
            self.assertEqual(mesh.version, armf.NO_HEADER_VERSION)
        self.assertEqual([m.bone_index for m in out.meshes], [1, 2, 3])
        self.assertEqual(armf.save(out), blob)

    def test_file_size_is_exactly_the_layout(self):
        mesh = self.make(vertices=10).meshes[0]
        blob = armf.save(armf.ArmFile(meshes=[mesh]))
        expected = (4                      # object count
                    + 2 + len("mast") * 2  # the single texture name
                    + 4 + 10 * 14 * 4      # vertex count, vertex block
                    + 4 + len(mesh.indices) * 4
                    + 4)                   # bone index
        self.assertEqual(len(blob), expected)

    def test_geometry_survives_but_colour_does_not(self):
        source = self.make(vertices=8).meshes[0]
        source.colours = np.ones((8, 4), np.float32)
        out = armf.load(armf.save(armf.ArmFile(meshes=[source])))
        result = out.meshes[0]
        for field in ("positions", "normals", "uv0", "tangents",
                      "binormals"):
            np.testing.assert_allclose(getattr(result, field),
                                       getattr(source, field), rtol=1e-6)
        # There is no colour block in the file, so it reads back zeroed -
        # but the array is still the right shape for the rest of the
        # add-on to use.
        self.assertEqual(result.colours.shape, (8, 4))
        self.assertFalse(result.colours.any())
        self.assertEqual(result.uv1.shape, (8, 2))

    def test_plain_rigid_model_form_with_bounding_box(self):
        """One object, no bone index, closing with a bounding box - the
        shape of the three vanilla testdata files that use this form."""
        arm = self.make(bone_index=None, objects=1)
        arm.bounding_box = arm.computed_bounding_box()
        blob = armf.save(arm)
        out = armf.load(blob)
        self.assertTrue(all(m.bone_index is None for m in out.meshes))
        np.testing.assert_allclose(out.bounding_box, arm.bounding_box,
                                   rtol=1e-6)
        self.assertEqual(armf.save(out), blob)

    def test_several_objects_without_bone_indices_roundtrip(self):
        """A headerless file has no magic to separate its objects, so
        whether they carry a bone index cannot be settled one object at a
        time - it is read as a property of the whole file, by trying both
        and keeping the reading that consumes it exactly. Two vanilla
        testdata files (loki, victory_test) are this shape."""
        arm = self.make(bone_index=None, objects=4)
        blob = armf.save(arm)
        out = armf.load(blob)
        self.assertEqual(len(out.meshes), 4)
        self.assertTrue(all(m.bone_index is None for m in out.meshes))
        self.assertEqual(armf.save(out), blob)

    def test_several_objects_with_bone_indices_roundtrip(self):
        """The other reading, which is what cannon_test_model uses."""
        arm = self.make(bone_index=1, objects=4)
        blob = armf.save(arm)
        out = armf.load(blob)
        self.assertEqual([m.bone_index for m in out.meshes], [1, 2, 3, 4])
        self.assertEqual(armf.save(out), blob)

    def test_mixing_bone_indices_refused(self):
        """The choice is per file, so a mixed list could not be read
        back whichever way the reader guessed."""
        arm = self.make(bone_index=1, objects=3)
        arm.meshes[1].bone_index = None
        with self.assertRaises(armf.ArmFormatError):
            armf.save(arm)

    def test_mixing_headerless_and_normal_objects_refused(self):
        """The flag is a property of the file, not of one object, so a
        mixed list could not be written back in any form."""
        arm = self.make(objects=2)
        arm.meshes[1].version = 5
        with self.assertRaises(armf.ArmFormatError):
            armf.save(arm)

    def test_normal_files_are_not_mistaken_for_headerless(self):
        """The detection must not fire on an ordinary file."""
        for version in (1, 3, 4, 5):
            mesh = (make_arm_mesh_v1(bone_index=1) if version == 1
                    else make_arm_mesh(version=version, bone_index=1))
            mesh.version = version
            if version < _PARAMS_FROM_VERSION_TEST:
                mesh.float_params = []
                mesh.vec4_params = []
            out = armf.load(armf.save(armf.ArmFile(meshes=[mesh])))
            self.assertEqual(out.meshes[0].version, version)

    def test_empty_file_is_not_headerless(self):
        """A zero-object file has no first object to look at; it must not
        be guessed either way."""
        arm = armf.ArmFile(meshes=[])
        arm.bounding_box = (0.0,) * 6
        blob = armf.save(arm)
        out = armf.load(blob)
        self.assertEqual(len(out.meshes), 0)
        self.assertEqual(armf.save(out), blob)


def make_vmpf(vertices=6, indices=None, version=3,
              vformat=None, skinned_pairs=None):
    """A small VMPF file, with two bone influences on selected vertices.

    The second influence's position is the first one seen through a fixed
    rigid transform, which is what the format actually means and what the
    bind-pose recovery relies on.
    """
    vformat = vf.VF_SKINNED if vformat is None else vformat
    model = vf.VmpfFile(version=version, vertex_format=vformat)
    stride = model.stride
    raw = np.zeros((vertices, stride), np.uint8)

    if vformat == vf.VF_SKINNED:
        halfs = raw.view(np.float16).reshape(vertices, -1)
        pos = (np.arange(vertices * 3, dtype=np.float32).reshape(-1, 3)
               * 0.125 - 0.5)
        halfs[:, 0:3] = pos.astype(np.float16)
        halfs[:, 3] = np.linspace(0, 1, vertices).astype(np.float16)
        halfs[:, 7] = np.linspace(1, 0, vertices).astype(np.float16)
        halfs[:, 8:12] = np.array([0.0, 0.0, 1.0, 1.0], np.float16)
        raw[:, 24:27] = 200          # normal
        raw[:, 27] = 3               # bone 0
        raw[:, 31] = 7               # bone 1
        raw[:, 32:35] = 90           # tangent
        raw[:, 36:39] = 160          # binormal
        raw[:, 35] = 255             # single influence by default
        for row in (skinned_pairs or []):
            raw[row, 35] = 128       # half and half
            halfs[row, 4:7] = (pos[row] + 0.25).astype(np.float16)
            raw[row, 28:31] = 210
    else:
        floats = raw.view(np.float32).reshape(vertices, -1)
        floats[:, 0:3] = (np.arange(vertices * 3, dtype=np.float32)
                          .reshape(-1, 3) * 0.125)
        floats[:, 7:9] = 0.5
        floats[:, 14:16] = 1.0
        raw[:, 16:19] = 200
        raw[:, 44:48] = 255

    if indices is None:
        indices = [i % vertices for i in range(((vertices // 3) * 3) or 3)]
    part = vf.VmpfPart(vertices=raw, indices=np.asarray(indices, np.uint16))
    if vformat == vf.VF_RIGID_NAMED:
        part.name = "rigid_equip_test_lod1"
        part.material_names = ["default", "default", "default"]
    model.parts.append(part)
    model.skeleton_name = "man_shogun"
    model.material_names = ["matt", "painted_metal", "cloth"]
    return model


class TestVmpfContainer(unittest.TestCase):
    """.variant_part_mesh - Shogun 2's skinned unit part format."""

    def test_roundtrip_skinned(self):
        model = make_vmpf(skinned_pairs=[1, 2])
        blob = vf.save(model)
        out = vf.load(blob)
        self.assertEqual(vf.save(out), blob)
        self.assertEqual(out.version, 3)
        self.assertEqual(out.vertex_format, vf.VF_SKINNED)
        self.assertEqual(out.skeleton_name, "man_shogun")
        self.assertEqual(out.material_names,
                         ["matt", "painted_metal", "cloth"])

    def test_roundtrip_every_vertex_format(self):
        for vformat in (vf.VF_RIGID, vf.VF_SKINNED, vf.VF_RIGID_NAMED):
            with self.subTest(vformat=vformat):
                blob = vf.save(make_vmpf(vformat=vformat))
                self.assertEqual(vf.save(vf.load(blob)), blob)

    def test_version_0_uses_the_short_skinned_vertex(self):
        """Version 0 predates the second influence's tangent frame."""
        self.assertEqual(make_vmpf(version=0).stride, 40)
        self.assertEqual(make_vmpf(version=3).stride, 48)
        blob = vf.save(make_vmpf(version=0))
        self.assertEqual(vf.save(vf.load(blob)), blob)

    def test_params_and_attachment_roundtrip(self):
        model = make_vmpf()
        model.float_params = [("light_scale", 1.0), ("bumpfactor", 2.0)]
        model.vec4_params = [("specfactor", (1.0, 1.0, 1.0, 1.0))]
        matrix = tuple(float(i) for i in range(16))
        model.attachment = ("crests", matrix, 21)
        out = vf.load(vf.save(model))
        self.assertEqual(out.float_params, model.float_params)
        self.assertEqual(out.vec4_params, model.vec4_params)
        self.assertEqual(out.attachment[0], "crests")
        self.assertEqual(out.attachment[2], 21)

    def test_attachment_precedes_the_parameters(self):
        """Order matters: reading the params first misplaces the bone
        index by the whole parameter block."""
        model = make_vmpf()
        model.float_params = [("light_scale", 1.0)]
        model.attachment = ("crests", tuple([0.0] * 16), 21)
        self.assertEqual(vf.load(vf.save(model)).attachment[2], 21)

    def test_declared_totals_preserved(self):
        """Two vanilla files declare zero totals but hold real geometry."""
        model = make_vmpf()
        model.declared_totals = (0, 0)
        blob = vf.save(model)
        self.assertEqual(struct.unpack_from("<2I", blob, 20), (0, 0))
        out = vf.load(blob)
        self.assertEqual(out.declared_totals, (0, 0))
        self.assertEqual(out.parts[0].vertex_count, 6)

    def test_bad_magic_rejected(self):
        with self.assertRaises(vf.VmpfError):
            vf.load(b"NOPE" + vf.save(make_vmpf())[4:])

    def test_out_of_range_index_rejected(self):
        model = make_vmpf(vertices=6, indices=[0, 1, 99])
        with self.assertRaises(vf.VmpfError):
            vf.load(vf.save(model))

    def test_unknown_version_rejected(self):
        blob = bytearray(vf.save(make_vmpf()))
        struct.pack_into("<I", blob, 4, 9)
        with self.assertRaises(vf.VmpfError):
            vf.load(bytes(blob))


class TestVmpfSkinning(unittest.TestCase):
    """The per-influence bone-local vertex, and rebuilding from it."""

    def test_weight_and_second_influence(self):
        model = make_vmpf(vertices=6, skinned_pairs=[1])
        ch = vf.decode_vertices(model, model.parts[0])
        self.assertEqual(list(ch["bone0"]), [3] * 6)
        self.assertAlmostEqual(float(ch["weight0"][0]), 1.0)
        self.assertAlmostEqual(float(ch["weight0"][1]), 128 / 255)
        # a single-influence vertex leaves its second position zeroed,
        # which is how the two cases are told apart
        self.assertTrue(np.all(ch["position1"][0] == 0))
        self.assertFalse(np.all(ch["position1"][1] == 0))

    def test_normals_are_stored_z_y_x(self):
        model = make_vmpf(vertices=3)
        model.parts[0].vertices[:, 24:27] = [10, 20, 30]
        ch = vf.decode_vertices(model, model.parts[0])
        decoded = ch["normal0"][0] * 127.5 + 127.5
        np.testing.assert_allclose(decoded, [30, 20, 10], atol=0.01)

    def test_skin_flags_missing_bones(self):
        """A bone with no frame must be reported, not silently placed at
        the origin as though it were correct."""
        model = make_vmpf(vertices=6)
        ch = vf.decode_vertices(model, model.parts[0])
        _, _, placed = vf.skin(ch, {})
        self.assertFalse(placed.any())
        _, _, placed = vf.skin(ch, {3: np.eye(4, dtype=np.float32)})
        self.assertTrue(placed.all())

    def test_bind_pose_recovered_from_geometry(self):
        """Two-influence vertices pin down the transform between two bone
        spaces, so the mesh can be assembled with no skeleton at all."""
        rng = np.random.default_rng(7)
        points = rng.normal(size=(24, 3)).astype(np.float32) * 0.4
        angle = 0.6
        rot = np.array([[np.cos(angle), -np.sin(angle), 0.0],
                        [np.sin(angle), np.cos(angle), 0.0],
                        [0.0, 0.0, 1.0]], np.float32)
        offset = np.array([0.25, -0.125, 0.5], np.float32)

        channels = {
            "position0": points,
            "position1": (points @ rot.T + offset).astype(np.float32),
            "bone0": np.full(24, 2, np.int32),
            "bone1": np.full(24, 5, np.int32),
            "weight0": np.full(24, 0.5, np.float32),
        }
        frames = vf.bind_frames_from_geometry(channels)
        self.assertEqual(set(frames), {2, 5})
        # placing both bones must map the two stored copies of the point
        # onto the same model-space position
        a = points @ frames[2][:3, :3].T + frames[2][:3, 3]
        b = channels["position1"] @ frames[5][:3, :3].T + frames[5][:3, 3]
        np.testing.assert_allclose(a, b, atol=1e-4)

    def test_bind_pose_needs_two_influence_vertices(self):
        model = make_vmpf(vertices=6)          # all single-influence
        ch = vf.decode_vertices(model, model.parts[0])
        self.assertEqual(vf.bind_frames_from_geometry(ch), {})


class TestVmpfRigidVertex(unittest.TestCase):
    """The stride-64 rigid vertex (formats 0 and 2)."""

    def make(self, count=4):
        rng = np.random.default_rng(11)
        positions = rng.normal(size=(count, 3)).astype(np.float32)
        directions = [
            utils.normalize_rows(rng.normal(size=(count, 3)).astype(
                np.float32)) for _ in range(3)]
        uv0 = rng.random((count, 2)).astype(np.float32)
        uv1 = rng.random((count, 2)).astype(np.float32)
        colours = np.ones((count, 4), np.float32)
        raw = vf.encode_rigid_vertices(positions, directions[0],
                                       directions[1], directions[2],
                                       uv0, uv1, colours)
        return raw, positions, directions, uv0, uv1

    def test_stride_and_constants(self):
        raw, _, _, _, _ = self.make()
        self.assertEqual(raw.shape[1], 64)
        floats = raw.view(np.float32).reshape(len(raw), 16)
        # (1.0, 1.0) closes every vanilla rigid vertex, and the eight
        # bytes before it are always zero
        np.testing.assert_allclose(floats[:, 14:16], 1.0)
        self.assertFalse(raw[:, 48:56].any())
        self.assertFalse(raw[:, 19].any())
        self.assertFalse(raw[:, 23].any())
        self.assertFalse(raw[:, 27].any())

    def test_positions_and_uvs_are_exact(self):
        """float32 positions mean the rigid format loses nothing."""
        raw, positions, _, uv0, uv1 = self.make()
        model = vf.VmpfFile(version=3, vertex_format=vf.VF_RIGID)
        part = vf.VmpfPart(vertices=raw,
                           indices=np.array([0, 1, 2], np.uint16))
        model.parts.append(part)
        channels = vf.decode_vertices(model, part)
        np.testing.assert_array_equal(channels["position0"], positions)
        np.testing.assert_array_equal(channels["uv"], uv0)
        np.testing.assert_array_equal(channels["uv1"], uv1)

    def test_directions_survive_byte_encoding(self):
        raw, _, directions, _, _ = self.make()
        model = vf.VmpfFile(version=3, vertex_format=vf.VF_RIGID)
        part = vf.VmpfPart(vertices=raw,
                           indices=np.array([0, 1, 2], np.uint16))
        model.parts.append(part)
        channels = vf.decode_vertices(model, part)
        for name, want in zip(("normal0", "tangent0", "binormal0"),
                              directions):
            dot = np.einsum("ij,ij->i",
                            utils.normalize_rows(channels[name]), want)
            self.assertGreater(dot.min(), 0.99, name)

    def test_direction_byte_order_is_z_y_x(self):
        one = np.array([[1.0, 0.0, 0.0]], np.float32)
        encoded = vf.encode_direction(one)[0]
        # x -> last byte
        self.assertEqual(int(encoded[2]), 255)

    def test_rigid_file_roundtrip(self):
        raw, _, _, _, _ = self.make(6)
        model = vf.VmpfFile(version=3, vertex_format=vf.VF_RIGID)
        model.parts.append(vf.VmpfPart(
            vertices=raw,
            indices=np.array([0, 1, 2, 3, 4, 5], np.uint16)))
        model.skeleton_name = ""
        model.material_names = ["default", "default", "default"]
        blob = vf.save(model)
        out = vf.load(blob)
        self.assertEqual(out.stride, 64)
        self.assertEqual(vf.save(out), blob)


def make_vwm_part(name="unit_head01", counts=(1, 2, 1, 3, 2, 1)):
    """A part whose vertices deliberately span several influence counts,
    since the variable stride is the whole difficulty of this format."""
    counts = np.asarray(counts, np.int32)
    n = len(counts)
    total = int(counts.sum())
    rng = np.arange(total, dtype=np.float32)

    part = wf.VwmPart(name=name)
    part.uv = np.arange(n * 2, dtype=np.float32).reshape(n, 2) * 0.017
    part.tangents = utils.normalize_rows(
        np.arange(n * 3, dtype=np.float32).reshape(n, 3) + 1.0)
    part.binormals = utils.normalize_rows(
        np.arange(n * 3, dtype=np.float32).reshape(n, 3)[:, ::-1] + 0.5)

    part.influence_counts = counts
    part.influence_bones = rng.astype(np.uint32) % 7
    part.influence_positions = (rng[:, None]
                                * [0.5, -0.25, 0.125]).astype(np.float32)
    part.influence_normals = utils.normalize_rows(
        (rng[:, None] + [1.0, 2.0, 3.0]).astype(np.float32))
    # Weights add to 1.0 within each vertex, as vanilla files do.
    weights = np.zeros(total, np.float32)
    start = 0
    for k in counts:
        weights[start:start + k] = 1.0 / k
        start += k
    part.influence_weights = weights

    tris = max(1, n // 3)
    part.indices = np.arange(tris * 3, dtype=np.uint32) % n
    return part


def make_vwm(parts=2, version=1):
    model = wf.VwmFile(version=version)
    model.float_params = [("light_scale", 1.0), ("bumpfactor", 0.5)]
    model.vec4_params = [("specfactor", (1.0, 1.0, 1.0, 1.0))]
    model.parts = [make_vwm_part(name="unit_part%02d" % i)
                   for i in range(parts)]
    return model


def one_part_model(part):
    model = wf.VwmFile(version=1)
    model.parts = [part]
    return model


class TestVwmContainer(unittest.TestCase):
    """Empire's .variant_weighted_mesh: parameter blocks, a part table,
    then one variable-stride vertex block per part."""

    def test_roundtrip(self):
        model = make_vwm()
        blob = wf.save(model)
        out = wf.load(blob)
        self.assertEqual(out.version, 1)
        self.assertEqual([p.name for p in out.parts],
                         [p.name for p in model.parts])
        self.assertEqual(wf.save(out), blob)

    def test_parameters_survive(self):
        out = wf.load(wf.save(make_vwm()))
        self.assertEqual([n for n, _ in out.float_params],
                         ["light_scale", "bumpfactor"])
        self.assertAlmostEqual(out.float_params[1][1], 0.5, places=6)
        self.assertEqual(out.vec4_params[0][0], "specfactor")

    def test_variable_influence_counts_survive(self):
        """The per-vertex influence count is what makes the stride vary,
        so it has to come back exactly."""
        model = make_vwm(parts=1)
        out = wf.load(wf.save(model))
        np.testing.assert_array_equal(out.parts[0].influence_counts,
                                      model.parts[0].influence_counts)
        np.testing.assert_allclose(out.parts[0].influence_weights,
                                   model.parts[0].influence_weights,
                                   rtol=1e-6)
        np.testing.assert_array_equal(out.parts[0].influence_bones,
                                      model.parts[0].influence_bones)

    def test_geometry_survives(self):
        model = make_vwm(parts=1)
        out = wf.load(wf.save(model))
        source, result = model.parts[0], out.parts[0]
        for field in ("uv", "tangents", "binormals", "influence_positions",
                      "influence_normals"):
            np.testing.assert_allclose(getattr(result, field),
                                       getattr(source, field), rtol=1e-6)
        np.testing.assert_array_equal(result.indices, source.indices)

    def test_headerless_variant(self):
        """12 vanilla files drop the magic, version and both parameter
        blocks and open straight at the part count."""
        model = make_vwm(version=wf.NO_HEADER_VERSION)
        blob = wf.save(model)
        self.assertNotEqual(struct.unpack_from("<I", blob, 0)[0],
                            wf.VWM_MAGIC)
        out = wf.load(blob)
        self.assertEqual(out.version, wf.NO_HEADER_VERSION)
        self.assertFalse(out.has_header)
        self.assertEqual(len(out.parts), len(model.parts))
        self.assertEqual(wf.save(out), blob)

    def test_zero_parts_allowed_only_with_a_header(self):
        """Empire ships one empty placeholder (unitmodels/euro_equipment);
        a headerless file opening with a zero has told us nothing
        checkable, so it is refused instead."""
        empty = wf.VwmFile(version=1)
        self.assertEqual(len(wf.load(wf.save(empty)).parts), 0)
        with self.assertRaises(wf.VwmFormatError):
            wf.load(struct.pack("<I", 0))

    def test_empty_attachment_section(self):
        """All but six vanilla files close with a bare zero count."""
        blob = wf.save(make_vwm(parts=1))
        self.assertEqual(blob[-4:], bytes(4))
        out = wf.load(blob)
        self.assertEqual(out.attachments, [])
        self.assertTrue(out.has_attachment_section)

    def test_bad_magic_rejected(self):
        with self.assertRaises(wf.VwmFormatError):
            wf.load(b"\xff\xff\xff\xff" + b"\x00" * 64)

    def test_out_of_range_index_rejected(self):
        model = make_vwm(parts=1)
        model.parts[0].indices = np.array([0, 1, 99], np.uint32)
        with self.assertRaises(wf.VwmFormatError):
            wf.load(wf.save(model))

    def test_influence_arrays_must_agree_with_counts(self):
        model = make_vwm(parts=1)
        model.parts[0].influence_weights = \
            model.parts[0].influence_weights[:-1]
        with self.assertRaises(wf.VwmFormatError):
            wf.save(model)

    def test_max_influences_enforced(self):
        part = make_vwm_part(counts=(1,))
        part.influence_counts = np.array([wf.MAX_INFLUENCES + 1], np.int32)
        with self.assertRaises(wf.VwmFormatError):
            wf.save(one_part_model(part))

    def test_colour_block_is_kept(self):
        """The 16 bytes closing every vertex are zero in all 1305 vanilla
        files, but they are a channel rather than padding, so a value put
        there has to survive the round trip."""
        part = make_vwm_part(counts=(1, 2))
        part.colours = np.array([[0.25, 0.5, 0.75, 1.0],
                                 [1.0, 0.0, 0.5, 0.125]], np.float32)
        out = wf.load(wf.save(one_part_model(part)))
        np.testing.assert_allclose(out.parts[0].colours, part.colours,
                                   rtol=1e-6)

    def test_colour_block_defaults_to_zero(self):
        """A part built without one still writes the vanilla bytes."""
        part = make_vwm_part(counts=(1,))
        part.colours = np.zeros((0, 4), np.float32)
        blob = wf.save(one_part_model(part))
        # Working back: the attachment count, the indices, the u32 index
        # count in front of them, then this one vertex's colour.
        colour_at = len(blob) - 4 - len(part.indices) * 4 - 4 - 16
        self.assertEqual(blob[colour_at:colour_at + 16], bytes(16))
        out = wf.load(blob)
        self.assertFalse(out.parts[0].colours.any())


class TestVwmSkinning(unittest.TestCase):
    """A VWM vertex has no model-space position at all: it is stored once
    per influence, in that bone's space, so reading one means skinning."""

    def _one_vertex(self, bones, weights):
        part = wf.VwmPart(name="p")
        part.uv = np.zeros((1, 2), np.float32)
        part.tangents = np.zeros((1, 3), np.float32)
        part.binormals = np.zeros((1, 3), np.float32)
        part.influence_counts = np.array([len(bones)], np.int32)
        part.influence_bones = np.array(bones, np.uint32)
        part.influence_positions = np.zeros((len(bones), 3), np.float32)
        part.influence_normals = np.zeros((len(bones), 3), np.float32)
        part.influence_weights = np.array(weights, np.float32)
        return part

    def test_single_influence_is_the_bone_transform(self):
        part = self._one_vertex([3], [1.0])
        part.influence_positions = np.array([[1.0, 0.0, 0.0]], np.float32)
        part.influence_normals = np.array([[0.0, 1.0, 0.0]], np.float32)

        frame = np.eye(4, dtype=np.float32)
        frame[:3, 3] = (10.0, 20.0, 30.0)
        positions, normals, placed = wf.skin(part, {3: frame})
        self.assertTrue(placed.all())
        np.testing.assert_allclose(positions[0], [11.0, 20.0, 30.0])
        # A pure translation must not move the normal.
        np.testing.assert_allclose(normals[0], [0.0, 1.0, 0.0])

    def test_two_influences_blend_by_weight(self):
        part = self._one_vertex([0, 1], [0.25, 0.75])
        a = np.eye(4, dtype=np.float32)
        a[:3, 3] = (4.0, 0.0, 0.0)
        b = np.eye(4, dtype=np.float32)
        b[:3, 3] = (0.0, 8.0, 0.0)
        positions, _, placed = wf.skin(part, {0: a, 1: b})
        self.assertTrue(placed.all())
        np.testing.assert_allclose(positions[0], [1.0, 6.0, 0.0], rtol=1e-6)

    def test_missing_bone_is_flagged_not_guessed(self):
        part = make_vwm_part(counts=(1,))
        part.influence_bones = np.array([5], np.uint32)
        _, _, placed = wf.skin(part, {})
        self.assertFalse(placed.any())

    def test_influence_table_pads_to_a_rectangle(self):
        """Blender wants one weight per (vertex, bone); short runs pad
        with zero weight so add_vertex_groups ignores them."""
        part = make_vwm_part(counts=(1, 3, 2))
        bones, weights = wf.influence_table(part)
        self.assertEqual(bones.shape, (3, 3))
        np.testing.assert_allclose(weights.sum(axis=1), 1.0, rtol=1e-6)
        self.assertEqual(int((weights[0] > 0).sum()), 1)
        self.assertEqual(int((weights[1] > 0).sum()), 3)
        self.assertEqual(int((weights[2] > 0).sum()), 2)

    def test_influence_table_keeps_bone_order(self):
        part = make_vwm_part(counts=(2, 1))
        bones, weights = wf.influence_table(part)
        np.testing.assert_array_equal(bones[0][:2],
                                      part.influence_bones[:2])
        self.assertEqual(int(bones[1][0]), int(part.influence_bones[2]))
        self.assertEqual(float(weights[1][1]), 0.0)

    def test_vertex_influences_slices_the_flat_arrays(self):
        part = make_vwm_part(counts=(1, 3))
        self.assertEqual(len(part.vertex_influences(0)), 1)
        self.assertEqual(len(part.vertex_influences(1)), 3)
        self.assertEqual(part.vertex_count, 2)


class TestVwmAttachments(unittest.TestCase):
    """The section that closes the file: the props a unit hangs off a
    single bone.  It is a lone zero in all but six vanilla files, so it
    read as padding at first - Empire's euro_equipment, which is 134
    muskets and flagpoles and no skinned parts at all, is what it turned
    out to be for."""

    def make(self, count=2, version=5):
        model = make_vwm(parts=1)
        for i in range(count):
            mesh = make_arm_mesh(version=version, bone_index=None,
                                 vertices=5)
            model.attachments.append(wf.VwmAttachment(
                name="rigid_equip_euro_musket%02d" % i, bone=2 + i,
                mesh=mesh))
        return model

    def test_round_trip(self):
        model = self.make()
        blob = wf.save(model)
        out = wf.load(blob)
        self.assertEqual([a.name for a in out.attachments],
                         [a.name for a in model.attachments])
        self.assertEqual([a.bone for a in out.attachments], [2, 3])
        self.assertEqual(wf.save(out), blob)

    def test_geometry_and_material_survive(self):
        model = self.make(count=1)
        source = model.attachments[0].mesh
        result = wf.load(wf.save(model)).attachments[0].mesh
        self.assertEqual(result.version, 5)
        self.assertEqual(result.textures, source.textures)
        self.assertEqual([n for n, _ in result.float_params],
                         [n for n, _ in source.float_params])
        np.testing.assert_allclose([v for _, v in result.float_params],
                                   [v for _, v in source.float_params],
                                   rtol=1e-6)
        for chan in ("positions", "normals", "uv0", "tangents",
                     "binormals", "colours", "uv1"):
            np.testing.assert_allclose(getattr(result, chan),
                                       getattr(source, chan), rtol=1e-6)
        np.testing.assert_array_equal(result.indices, source.indices)

    def test_bone_is_not_the_objects_own_trailing_index(self):
        """The bone comes before the object here, not after it - so the
        embedded object must not also carry one."""
        model = self.make(count=1)
        out = wf.load(wf.save(model))
        self.assertIsNone(out.attachments[0].mesh.bone_index)
        self.assertEqual(out.attachments[0].bone, 2)

    def test_attachments_only_file(self):
        """unitmodels/euro_equipment has no skinned parts whatsoever."""
        model = self.make(count=3)
        model.parts = []
        out = wf.load(wf.save(model))
        self.assertEqual(out.parts, [])
        self.assertEqual(len(out.attachments), 3)

    def test_headerless_attachments_have_no_texture_slots(self):
        """CA's older testdata files store version 0 objects here, and
        those drop the texture name a standalone version 0 object has."""
        model = self.make(count=1, version=armf.NO_HEADER_VERSION)
        model.attachments[0].mesh.textures = ["", "", "", ""]
        model.attachments[0].mesh.float_params = []
        model.attachments[0].mesh.vec4_params = []
        blob = wf.save(model)
        out = wf.load(blob)
        self.assertEqual(out.attachments[0].mesh.version,
                         armf.NO_HEADER_VERSION)
        self.assertEqual(out.attachments[0].name,
                         model.attachments[0].name)
        self.assertEqual(wf.save(out), blob)

    def test_undecodable_section_is_kept_verbatim(self):
        """A section this reader cannot make sense of must still re-save
        unchanged rather than fail the file."""
        blob = bytearray(wf.save(make_vwm(parts=1)))
        blob[-4:] = struct.pack("<I", 3)      # claims three attachments
        blob += b"nonsense that is not an object"
        out = wf.load(bytes(blob))
        self.assertEqual(out.attachments, [])
        self.assertIsNotNone(out.attachments_raw)
        self.assertEqual(wf.save(out), bytes(blob))


class TestVwmOlderLayouts(unittest.TestCase):
    """Three earlier generations of the container live in CA's testdata
    folders.  Each drops one thing from the shipping layout, and which is
    which is decided by trying them and keeping the shape that consumes
    the file exactly."""

    def make(self, **shape):
        model = make_vwm(parts=2)
        model.version = wf.NO_HEADER_VERSION
        model.float_params = []
        model.vec4_params = []
        for key, value in shape.items():
            setattr(model, key, value)
        return model

    def test_shipping_shape_is_the_default(self):
        model = wf.VwmFile()
        self.assertTrue(model.has_names)
        self.assertTrue(model.has_tangent_frame)
        self.assertTrue(model.has_colour)

    def test_named_without_colour(self):
        model = self.make(has_colour=False)
        out = wf.load(wf.save(model))
        self.assertFalse(out.has_colour)
        self.assertTrue(out.has_names)
        self.assertEqual([p.name for p in out.parts],
                         [p.name for p in model.parts])
        self.assertFalse(out.parts[0].colours.any())

    def test_nameless_table(self):
        model = self.make(has_names=False, has_colour=False,
                          has_attachment_section=False)
        blob = wf.save(model)
        # the part count, then the two totals, and no names anywhere
        self.assertEqual(struct.unpack_from("<3I", blob, 0),
                         (2, sum(p.vertex_count for p in model.parts),
                          sum(len(p.indices) for p in model.parts)))
        out = wf.load(blob)
        self.assertFalse(out.has_names)
        self.assertEqual([p.name for p in out.parts], ["", ""])
        self.assertEqual(len(out.parts[0].influence_counts),
                         len(model.parts[0].influence_counts))
        self.assertEqual(wf.save(out), blob)

    def test_nameless_without_tangent_frame(self):
        model = self.make(has_names=False, has_tangent_frame=False,
                          has_colour=False, has_attachment_section=False)
        out = wf.load(wf.save(model))
        self.assertFalse(out.has_tangent_frame)
        np.testing.assert_allclose(out.parts[0].uv, model.parts[0].uv,
                                   rtol=1e-6)
        self.assertFalse(out.parts[0].tangents.any(),
                         "this layout stores no tangent frame to read")

    def test_totals_must_agree_with_the_parts(self):
        model = self.make(has_names=False, has_colour=False,
                          has_attachment_section=False)
        blob = bytearray(wf.save(model))
        struct.pack_into("<I", blob, 4, 999)
        with self.assertRaises(wf.VwmFormatError):
            wf.load(bytes(blob))

    def test_the_shapes_do_not_collide(self):
        """Every layout must read back as itself, or the try-each-shape
        decision would be picking the wrong one somewhere."""
        for names, colour in ((True, True), (True, False), (False, False)):
            with self.subTest(names=names, colour=colour):
                model = self.make(has_names=names, has_colour=colour,
                                  has_attachment_section=names)
                out = wf.load(wf.save(model))
                self.assertEqual((out.has_names, out.has_colour),
                                 (names, colour))

    def test_more_influences_than_shipping_files_use(self):
        """The oldest files put up to 11 influences on a vertex where
        anything that shipped stops at 8."""
        part = make_vwm_part(counts=(11,))
        model = one_part_model(part)
        out = wf.load(wf.save(model))
        self.assertEqual(int(out.parts[0].influence_counts[0]), 11)


class TestRigidModelAnimation(unittest.TestCase):
    """.rigid_model_animation is not a new container: it is the
    .animatable_rigid_model object list followed by a whole headerless
    .anim.  arm_format keeps that tail so both halves round-trip."""

    def objects(self, count=2):
        return armf.ArmFile(meshes=[
            make_arm_mesh(bone_index=i, vertices=6) for i in range(count)])

    def test_trailing_bytes_are_kept_and_written_back(self):
        arm = self.objects()
        tail = make_anim_v0()
        blob = armf.save(arm) + tail

        with self.assertRaises(armf.ArmFormatError):
            armf.load(blob)               # the tail is not ours by default

        out = armf.load(blob, allow_trailing=True)
        self.assertEqual(out.trailing, tail)
        self.assertEqual(len(out.meshes), 2)
        self.assertEqual(armf.save(out), blob)

    def test_the_tail_parses_as_a_headerless_anim(self):
        tail = make_anim_v0()
        out = armf.load(armf.save(self.objects(1)) + tail,
                        allow_trailing=True)
        anim = af.load(out.trailing)
        self.assertEqual(anim.version, af.SHOGUN2_NO_HEADER_VERSION)
        self.assertEqual(af.save(anim), tail)

    def test_plain_files_have_no_trailing_bytes(self):
        out = armf.load(armf.save(self.objects(1)), allow_trailing=True)
        self.assertEqual(out.trailing, b"")

    def test_bone_indices_survive_the_split(self):
        """The last object's bone index sits immediately before the
        animation, which is exactly where the reader could lose it."""
        arm = self.objects(3)
        out = armf.load(armf.save(arm) + make_anim_v0(),
                        allow_trailing=True)
        self.assertEqual([m.bone_index for m in out.meshes], [0, 1, 2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
