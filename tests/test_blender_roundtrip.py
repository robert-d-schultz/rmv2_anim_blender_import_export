"""Headless Blender integration test for the RMV2 add-on.

Run with:
    blender --background --factory-startup --python tests/test_blender_roundtrip.py

Builds synthetic .rigid_model_v2 files, imports them with the add-on,
exports them again and verifies the result semantically (triangle soup,
weights, materials, LOD metadata). Also exports a native Blender cube.
Exits non-zero on failure.
"""

import os
import sys
import tempfile
import traceback

REPO = os.path.abspath(os.path.join(os.path.dirname(
    os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "io_scene_rmv2"))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

import io_scene_rmv2  # noqa: E402
from io_scene_rmv2 import anim_format as af  # noqa: E402
from io_scene_rmv2 import export_anim, export_rmv2  # noqa: E402
from io_scene_rmv2 import import_anim, import_rmv2  # noqa: E402
from io_scene_rmv2 import materials as rmv2_materials  # noqa: E402
from io_scene_rmv2 import rmv2_format as rf  # noqa: E402
from io_scene_rmv2 import skeleton as rmv2_skeleton  # noqa: E402
from io_scene_rmv2 import utils  # noqa: E402

FAILURES = []


def check(condition, message):
    if condition:
        print(f"  ok  - {message}")
    else:
        print(f"  FAIL- {message}")
        FAILURES.append(message)


# ---------------------------------------------------------------------------
# Synthetic file builder (mirrors tests/test_format.py, kept independent)
# ---------------------------------------------------------------------------

def make_cube_mesh(weight_count):
    corners = np.array([
        [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
        [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
    ], dtype=np.float32) * 0.72

    mesh = rf.RmvMeshData.empty(8, weight_count)
    mesh.positions = corners
    normals = corners / np.linalg.norm(corners, axis=1, keepdims=True)
    mesh.normals = normals.astype(np.float32)
    t = np.cross(normals, [0.0, 1.0, 0.001])
    mesh.tangents = (t / np.linalg.norm(t, axis=1, keepdims=True)
                     ).astype(np.float32)
    b = np.cross(normals, t)
    mesh.binormals = (b / np.linalg.norm(b, axis=1, keepdims=True)
                      ).astype(np.float32)
    mesh.uv0 = np.linspace(0.1, 0.9, 16, dtype=np.float32).reshape(8, 2)
    mesh.uv1 = np.linspace(0.9, 0.1, 16, dtype=np.float32).reshape(8, 2)
    mesh.colours = np.round(np.linspace(0, 255, 32)).reshape(8, 4) \
        .astype(np.float32) / 255.0
    if weight_count:
        mesh.bone_indices = np.zeros((8, weight_count), np.uint8)
        mesh.bone_weights = np.zeros((8, weight_count), np.float32)
        mesh.bone_indices[:, 0] = 0
        mesh.bone_indices[:, 1] = 3
        mesh.bone_weights[:, 0] = 0.7
        mesh.bone_weights[:, 1] = 0.3
    quads = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    tris = []
    for q in quads:
        tris += [q[0], q[1], q[2], q[0], q[2], q[3]]
    mesh.indices = np.array(tris, dtype=np.uint16)
    return mesh


def make_file(version, vertex_format, with_attach_points=True):
    rmv = rf.RmvFile(version=version, skeleton_name="humanoid01"
                     if vertex_format != rf.VF_STATIC else "")
    wc = {rf.VF_CINEMATIC: 4, rf.VF_WEIGHTED: 2}.get(vertex_format, 0)
    for i in range(2):
        lod = rf.RmvLod(camera_distance=(i + 1) * 40.0, lod_level=i)
        mat = rf.WeightedMaterial(
            material_id=(rf.MAT_WEIGHTED if wc else rf.MAT_DEFAULT),
            vertex_format=vertex_format,
            model_name="test_mesh",
            texture_directory="variantmeshes\\test",
            pivot=(0.25, 1.5, -0.75),
        )
        mat.textures = [(0, "variantmeshes\\test\\diffuse.dds"),
                        (1, "variantmeshes\\test\\normal.dds"),
                        (27, "variantmeshes\\test\\base_colour.dds")]
        if wc and with_attach_points:
            mat.attachment_points = [
                rf.RmvAttachmentPoint(name="root", bone_index=0),
                rf.RmvAttachmentPoint(name="spine_0", bone_index=3),
            ]
        mat.int_params = [(rf.INT_PARAM_ALPHA, 1)]
        mat.vec4_params = [(0, (0.0, 0.25, 0.5, 1.0))]
        lod.models.append(rf.RmvModel(material=mat,
                                      mesh=make_cube_mesh(wc)))
        rmv.lods.append(lod)
    return rmv


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

def canonical_triangles(model, with_weights, with_colour=True):
    """Order-independent triangle soup with per-vertex data, rounded to
    survive quantization differences."""
    mesh = model.mesh
    tris = mesh.indices.reshape(-1, 3)
    out = []
    for tri in tris:
        verts = []
        for vi in tri:
            entry = (
                tuple(np.round(mesh.positions[vi], 2).tolist()),
                tuple(np.round(mesh.normals[vi], 1).tolist()),
                tuple(np.round(mesh.uv0[vi], 2).tolist()),
            )
            if with_colour:
                entry = entry + (
                    tuple(np.round(mesh.colours[vi], 2).tolist()),)
            if with_weights:
                pairs = sorted(
                    (int(b), round(float(w), 2))
                    for b, w in zip(mesh.bone_indices[vi],
                                    mesh.bone_weights[vi]) if w > 0)
                entry = entry + (tuple(pairs),)
            verts.append(entry)
        k = min(range(3), key=lambda i: verts[i])
        out.append(tuple(verts[k:] + verts[:k]))
    return sorted(out)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def activate_collection(root_name):
    def find(layer_col):
        if layer_col.collection.name == root_name:
            return layer_col
        for child in layer_col.children:
            hit = find(child)
            if hit is not None:
                return hit
        return None

    layer = find(bpy.context.view_layer.layer_collection)
    assert layer is not None, f"collection {root_name} not in view layer"
    bpy.context.view_layer.active_layer_collection = layer


def layer_collection_hidden(collection_name):
    def find(layer_col):
        if layer_col.collection.name == collection_name:
            return layer_col
        for child in layer_col.children:
            hit = find(child)
            if hit is not None:
                return hit
        return None

    layer = find(bpy.context.view_layer.layer_collection)
    assert layer is not None, f"collection {collection_name} not in view layer"
    return layer.hide_viewport


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def roundtrip_case(tmpdir, version, vertex_format, label):
    print(f"\n=== Roundtrip {label} (v{version}) ===")
    reset_scene()

    src_path = os.path.join(tmpdir, f"src_{label}_v{version}.rigid_model_v2")
    dst_path = os.path.join(tmpdir, f"dst_{label}_v{version}.rigid_model_v2")
    original = make_file(version, vertex_format)
    with open(src_path, "wb") as handle:
        handle.write(rf.save(original))

    # ---- import ----
    root, stats = import_rmv2.import_file(bpy.context, src_path, {
        "import_lods": "ALL",
        "build_materials": True,
        "texture_root": "",
        "create_attach_empties": False,
        "global_scale": 1.0,
    })
    check(stats["meshes"] == 2, "imported 2 meshes")
    check(stats["lods"] == 2, "imported 2 lods")
    check(root.rmv2.is_rmv2_root, "root collection flagged")
    check(root.rmv2.skeleton_name == original.skeleton_name,
          "skeleton name preserved")
    check(not layer_collection_hidden(root.children[0].name),
          "most detailed LOD (lod0) left visible")
    check(layer_collection_hidden(root.children[1].name),
          "other LOD collections (lod1) hidden by default")

    bpy.context.view_layer.update()   # sync matrix_world of new objects
    lod0 = root.children[0]
    obj = lod0.objects[0]
    check(len(obj.data.vertices) == 8, "8 vertices imported (no welding)")
    check(len(obj.data.polygons) == 12, "12 triangles imported")

    # world-space positions must match the file
    mesh_file = original.lods[0].models[0].mesh
    world = np.array([obj.matrix_world @ v.co for v in obj.data.vertices],
                     dtype=np.float32)
    expected = utils.game_to_blender(mesh_file.positions)
    err = np.abs(np.sort(world.ravel()) - np.sort(expected.ravel())).max()
    check(err < 2e-3, f"world positions match file (max err {err:.5f})")

    pivot_b = utils.game_to_blender_v(
        original.lods[0].models[0].material.pivot)
    check(np.allclose(obj.location, pivot_b, atol=1e-5),
          "pivot became object origin")

    if vertex_format != rf.VF_STATIC:
        names = {g.name for g in obj.vertex_groups}
        check(names == {"bone_0", "bone_3"},
              f"vertex groups get standard bone_<i> names ({names})")
        check([(a.name, a.bone_index) for a in root.rmv2.attach_points]
              == [("root", 0), ("spine_0", 3)],
              "attachment points stored as a list on the root collection")
    check(len(obj.rmv2.textures) == 3, "texture slots imported")
    check(obj.rmv2.alpha_mode == "TRANSPARENT", "alpha mode imported")
    check(obj.data.materials and obj.data.materials[0].use_nodes,
          "Blender material built")
    if vertex_format == rf.VF_STATIC:
        check(len(obj.data.uv_layers) == 2, "static mesh got 2 UV layers")

    # ---- export ----
    activate_collection(root.name)
    stats, warnings = export_rmv2.export_file(bpy.context, dst_path, {
        "source": "AUTO",
        "version": str(version),
        "skeleton_name": root.rmv2.skeleton_name,
        "apply_modifiers": True,
        "high_precision": True,
        "write_attach_points": True,
        "global_scale": 1.0,
    })
    print("  export warnings:", warnings or "none")
    check(stats["lods"] == 2, "exported 2 lods")
    check(stats["meshes"] == 2, "exported 2 meshes")

    # ---- compare ----
    with open(dst_path, "rb") as handle:
        result = rf.load(handle.read())
    check(result.version == version, "version kept")
    check(result.skeleton_name == original.skeleton_name, "skeleton kept")
    check(len(result.lods) == len(original.lods), "lod count kept")

    with_weights = vertex_format in (rf.VF_WEIGHTED, rf.VF_CINEMATIC)
    for li, (lod_in, lod_out) in enumerate(zip(original.lods, result.lods)):
        check(abs(lod_in.camera_distance - lod_out.camera_distance) < 1e-4,
              f"lod{li} camera distance kept")
        m_in, m_out = lod_in.models[0], lod_out.models[0]
        check(m_out.material.vertex_format == vertex_format,
              f"lod{li} vertex format kept")
        check(m_out.material.material_id == m_in.material.material_id,
              f"lod{li} material id kept")
        check(m_out.material.model_name == m_in.material.model_name,
              f"lod{li} model name kept")
        check(m_out.material.textures == m_in.material.textures,
              f"lod{li} textures kept")
        check(m_out.material.texture_directory
              == m_in.material.texture_directory,
              f"lod{li} texture dir kept")
        check(m_out.material.get_int_param(rf.INT_PARAM_ALPHA) == 1,
              f"lod{li} alpha param kept")
        check(m_out.material.vec4_params == m_in.material.vec4_params,
              f"lod{li} vec4 params kept")
        np.testing.assert_allclose(m_out.material.pivot, m_in.material.pivot,
                                   atol=1e-5)
        if with_weights:
            check([(a.name, a.bone_index)
                   for a in m_out.material.attachment_points]
                  == [("root", 0), ("spine_0", 3)],
                  f"lod{li} attachment points kept")

        # colour is only persisted by the static layout and v8 skinned ones
        with_colour = vertex_format == rf.VF_STATIC or version == 8
        tris_in = canonical_triangles(m_in, with_weights, with_colour)
        tris_out = canonical_triangles(m_out, with_weights, with_colour)
        check(len(tris_out) == len(tris_in),
              f"lod{li} triangle count kept")
        check(tris_in == tris_out,
              f"lod{li} triangle soup identical "
              "(positions/normals/uvs/colours/weights, winding)")
        if tris_in != tris_out:
            for a, b in zip(tris_in, tris_out):
                if a != b:
                    print("   first mismatch:")
                    print("   in :", a)
                    print("   out:", b)
                    break


def native_export_case(tmpdir):
    print("\n=== Native Blender mesh export ===")
    reset_scene()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    sphere = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    path = os.path.join(tmpdir, "native.rigid_model_v2")
    sphere.select_set(True)
    stats, warnings = export_rmv2.export_file(bpy.context, path, {
        "source": "SELECTED",
        "version": "8",
        "skeleton_name": "",
        "apply_modifiers": True,
        "high_precision": False,
        "write_attach_points": True,
        "global_scale": 1.0,
    })
    print("  export warnings:", warnings or "none")
    check(stats["lods"] == 1, "flat selection exports a single LOD "
          "(no duplication)")
    with open(path, "rb") as handle:
        result = rf.load(handle.read())
    model = result.lods[0].models[0]
    check(model.material.vertex_format == rf.VF_STATIC,
          "AUTO resolved to Static for unskinned mesh")
    check(model.material.material_id == rf.MAT_DEFAULT,
          "AUTO material became default_type")
    check(model.mesh.vertex_count > 0 and len(model.mesh.indices) > 0,
          "geometry written")
    # sphere of radius 1: positions must stay on the unit sphere
    radii = np.linalg.norm(model.mesh.positions, axis=1)
    check(abs(radii.max() - 1.0) < 5e-3 and abs(radii.min() - 1.0) < 5e-3,
          "positions survive coordinate conversion (unit sphere)")
    # normals should point outward, roughly parallel to positions
    dots = (utils.normalize_rows(model.mesh.positions)
            * model.mesh.normals).sum(axis=1)
    check(dots.min() > 0.5, f"normals outward (min dot {dots.min():.3f})")


def auto_lod_case(tmpdir):
    print("\n=== Auto-LOD generation (decimate) ===")
    reset_scene()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    sphere = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    path = os.path.join(tmpdir, "auto_lod.rigid_model_v2")
    sphere.select_set(True)
    stats, warnings = export_rmv2.export_file(bpy.context, path, {
        "source": "SELECTED",
        "version": "7",
        "skeleton_name": "",
        "auto_lods": True,
        "auto_lod_count": 4,
        "apply_modifiers": True,
        "high_precision": False,
        "write_attach_points": True,
        "global_scale": 1.0,
    })
    print("  export warnings:", warnings or "none")
    check(stats["lods"] == 4, f"4 LODs generated ({stats['lods']})")

    with open(path, "rb") as handle:
        result = rf.load(handle.read())
    tri_counts = [len(lod.models[0].mesh.indices) // 3
                  for lod in result.lods]
    print("  triangle counts per LOD:", tri_counts)
    check(all(a > b for a, b in zip(tri_counts, tri_counts[1:])),
          f"each LOD has fewer triangles than the last ({tri_counts})")
    check(tri_counts[-1] <= tri_counts[0] * 0.25,
          "last LOD is substantially reduced")
    check([round(lod.camera_distance) for lod in result.lods]
          == [20, 40, 80, 160], "default camera distances assigned")
    check([lod.lod_level for lod in result.lods] == [0, 1, 2, 3],
          "lod levels sequential")
    # every LOD must still be a sphere-ish blob of radius ~1 (collapse
    # decimation shifts vertices slightly in both directions)
    for i, lod in enumerate(result.lods):
        radii = np.linalg.norm(lod.models[0].mesh.positions, axis=1)
        check(radii.max() < 1.1 and radii.min() > 0.75,
              f"lod{i} geometry still spherical "
              f"({radii.min():.2f}..{radii.max():.2f})")
    # the temporary decimate modifier must be gone
    check(len(sphere.modifiers) == 0,
          "temporary decimate modifiers cleaned up")

    # asking for auto LODs when the source already has LOD collections
    # only warns
    reset_scene()
    src = os.path.join(tmpdir, "auto_lod_src.rigid_model_v2")
    with open(src, "wb") as handle:
        handle.write(rf.save(make_file(7, rf.VF_STATIC)))
    root, _ = import_rmv2.import_file(bpy.context, src, {
        "import_lods": "ALL", "build_materials": False})
    activate_collection(root.name)
    dst = os.path.join(tmpdir, "auto_lod_multi.rigid_model_v2")
    stats, warnings = export_rmv2.export_file(bpy.context, dst, {
        "source": "AUTO", "version": "7", "skeleton_name": "",
        "auto_lods": True, "auto_lod_count": 4,
        "apply_modifiers": True, "high_precision": False,
        "write_attach_points": True, "global_scale": 1.0})
    check(stats["lods"] == 2 and any("Generate LODs ignored" in w
                                     for w in warnings),
          "auto LODs skipped with a warning for multi-LOD sources")


def skinned_native_case(tmpdir):
    """Blender-authored skinned mesh with an armature, bone-name groups."""
    print("\n=== Native skinned mesh export ===")
    reset_scene()
    arm_data = bpy.data.armatures.new("skel")
    arm_obj = bpy.data.objects.new("skel", arm_data)
    bpy.context.scene.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    for i, name in enumerate(("root", "spine_0", "spine_1")):
        bone = arm_data.edit_bones.new(name)
        bone.head = (0, 0, i * 0.5)
        bone.tail = (0, 0, i * 0.5 + 0.5)
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    mod = cube.modifiers.new("arm", "ARMATURE")
    mod.object = arm_obj
    vg0 = cube.vertex_groups.new(name="root")
    vg2 = cube.vertex_groups.new(name="spine_1")
    vg_junk = cube.vertex_groups.new(name="not_a_bone")
    all_verts = list(range(len(cube.data.vertices)))
    vg0.add(all_verts, 0.75, "REPLACE")
    vg2.add(all_verts, 0.25, "REPLACE")
    vg_junk.add(all_verts, 1.0, "REPLACE")

    path = os.path.join(tmpdir, "skinned.rigid_model_v2")
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    stats, warnings = export_rmv2.export_file(bpy.context, path, {
        "source": "SELECTED",
        "version": "7",
        "skeleton_name": "humanoid01",
        "apply_modifiers": True,
        "high_precision": True,
        "write_attach_points": True,
        "global_scale": 1.0,
    })
    print("  export warnings:", warnings or "none")
    with open(path, "rb") as handle:
        result = rf.load(handle.read())
    model = result.lods[0].models[0]
    check(model.material.vertex_format == rf.VF_CINEMATIC,
          "AUTO resolved to Cinematic for skinned mesh")
    check(model.material.material_id == rf.MAT_WEIGHTED,
          "AUTO material became weighted")
    aps = [(a.name, a.bone_index) for a in model.material.attachment_points]
    check(aps == [("root", 0), ("spine_0", 1), ("spine_1", 2)],
          f"attachment points generated from armature ({aps})")
    pairs = {(int(b), round(float(w), 2))
             for b, w in zip(model.mesh.bone_indices[0],
                             model.mesh.bone_weights[0]) if w > 0}
    check(pairs == {(0, 0.75), (2, 0.25)},
          f"weights map to armature bone indices ({pairs})")
    check(any("not_a_bone" in w for w in warnings),
          "unmapped vertex group produced a warning")


def material_node_case():
    print("\n=== Material node graph (Mask + MaterialMap) ===")
    reset_scene()

    textures = [
        (27, "variantmeshes\\test\\base_colour.dds"),   # BaseColour
        (1, "variantmeshes\\test\\normal.dds"),          # Normal
        (3, "variantmeshes\\test\\mask.dds"),            # Mask
        (29, "variantmeshes\\test\\material_map.dds"),   # MaterialMap
        (11, "variantmeshes\\test\\specular.dds"),       # Specular
    ]
    mat = rmv2_materials.build_material("probe_mat", textures, "OPAQUE", "")
    tree = mat.node_tree
    bsdf = tree.nodes["Principled BSDF"]

    def link_source(socket):
        for link in tree.links:
            if link.to_socket == socket:
                return link.from_node
        return None

    base_src = link_source(bsdf.inputs["Base Color"])
    check(base_src is not None and base_src.type == "MIX_RGB",
          "Base Color fed by the mask mix chain, not the raw texture")

    mix_chain = []
    node = base_src
    while node is not None and node.type == "MIX_RGB":
        mix_chain.append(node)
        node = link_source(node.inputs["Color1"])
    check(len(mix_chain) == 3, f"3-stage player-colour mix chain "
          f"({len(mix_chain)} stages found)")
    check(node is not None and node.label.startswith("BaseColour"),
          "mix chain bottoms out at the BaseColour texture")

    sep_nodes = [n for n in tree.nodes
                if n.type in ("SEPARATE_COLOR", "SEPRGB")]
    check(len(sep_nodes) == 2,
          f"two Separate Color nodes created (mask + material map), "
          f"found {len(sep_nodes)}")

    mask_tex_node = tree.nodes.get("Mask")
    mask_sep_node = link_source(mix_chain[0].inputs["Fac"]) \
        if mix_chain else None
    check(mask_sep_node is not None
          and mask_sep_node.type in ("SEPARATE_COLOR", "SEPRGB"),
          "bottom-of-chain mix's factor fed by a Separate Color node")
    if mask_sep_node is not None:
        mask_sep_input_src = link_source(mask_sep_node.inputs["Color"])
        check(mask_sep_input_src is not None
              and mask_tex_node is not None
              and mask_sep_input_src.name == mask_tex_node.name,
              "mask texture's Color output is actually linked into the "
              "Separate Color node (the mix factors aren't stuck at 0)")

    metallic_src = link_source(bsdf.inputs["Metallic"])
    roughness_src = link_source(bsdf.inputs["Roughness"])
    check(metallic_src is not None and metallic_src.type in
          ("SEPARATE_COLOR", "SEPRGB"),
          "Metallic fed from the MaterialMap's Separate Color node")
    check(roughness_src is not None and roughness_src.type in
          ("SEPARATE_COLOR", "SEPRGB"),
          "Roughness fed from the MaterialMap's Separate Color node "
          "(not the Gloss-invert fallback, since MaterialMap takes "
          "priority)")
    check(not any(n.type == "INVERT" for n in tree.nodes),
          "no Gloss-invert node created when MaterialMap is present")

    strength_src = link_source(bsdf.inputs["Emission Strength"])
    mask_node = tree.nodes.get("Mask")
    check(mask_node is not None, "mask texture node present")
    check(strength_src is not None and strength_src.name == mask_node.name,
          "Emission Strength fed directly from the mask's alpha channel")

    normal_src = link_source(bsdf.inputs["Normal"])
    check(normal_src is not None and normal_src.type == "NORMAL_MAP",
          "Normal still wired through a Normal Map node")

    spec_in = bsdf.inputs.get("Specular IOR Level")
    check(spec_in is not None and link_source(spec_in) is not None,
          "Specular still wired to Specular IOR Level")

    # --- Layout sanity: everything on the rounded 300-unit grid, and no
    # two nodes sharing the exact same spot. (Must run before the next
    # reset_scene(), which frees this material's node tree.) ---
    step = 300
    off_grid = [n.name for n in tree.nodes
               if n.location.x % step != 0 or n.location.y % step != 0]
    check(not off_grid, f"all node locations sit on the {step}-unit grid "
          f"(off-grid: {off_grid})")
    locations = [(round(n.location.x), round(n.location.y))
                for n in tree.nodes]
    check(len(locations) == len(set(locations)),
          "no two nodes share the exact same location")
    collapsed = {n.name for n in tree.nodes if n.hide}
    expected_collapsed_types = {"SEPARATE_COLOR", "SEPRGB", "MIX_RGB",
                               "NORMAL_MAP", "INVERT"}
    check(collapsed and all(
        tree.nodes[n].type in expected_collapsed_types for n in collapsed),
        f"intermediate utility nodes are collapsed for a compact graph "
        f"({sorted(collapsed)})")

    # --- Gloss fallback: MaterialMap absent, Gloss present ---
    reset_scene()
    textures2 = [(27, "x\\base_colour.dds"), (12, "x\\gloss.dds")]
    mat2 = rmv2_materials.build_material("probe_mat2", textures2, "OPAQUE",
                                         "")
    tree2 = mat2.node_tree
    bsdf2 = tree2.nodes["Principled BSDF"]
    rough_src2 = None
    for link in tree2.links:
        if link.to_socket == bsdf2.inputs["Roughness"]:
            rough_src2 = link.from_node
    check(rough_src2 is not None and rough_src2.type == "INVERT",
          "Gloss-invert fallback still used when no MaterialMap present")

    # --- No texture at all: a plain white RGB node acts as base colour ---
    reset_scene()
    mat3 = rmv2_materials.build_material("probe_mat3", [], "OPAQUE", "")
    tree3 = mat3.node_tree
    bsdf3 = tree3.nodes["Principled BSDF"]
    white3 = None
    for link in tree3.links:
        if link.to_socket == bsdf3.inputs["Base Color"]:
            white3 = link.from_node
    check(white3 is not None and white3.type == "RGB",
          "a white RGB placeholder feeds Base Color when there are no "
          "textures at all")
    check(white3 is not None
          and tuple(white3.outputs[0].default_value) == (1.0, 1.0, 1.0, 1.0),
          "the placeholder's colour is clean white")
    check(len(tree3.nodes) == 3,  # Output + Principled BSDF + placeholder
          f"exactly one extra node created for an empty texture list "
          f"({len(tree3.nodes)} nodes)")

    # --- Mask present but no base colour texture: same white placeholder,
    # this time feeding the bottom of the mix chain instead of the BSDF
    # directly. There's no special-cased "mask-only" behaviour - it's the
    # same fallback rule as above, just consumed by a different link. ---
    reset_scene()
    mat4 = rmv2_materials.build_material(
        "probe_mat4", [(3, "x\\mask.dds")], "OPAQUE", "")
    tree4 = mat4.node_tree
    bsdf4 = tree4.nodes["Principled BSDF"]
    base_src4 = None
    for link in tree4.links:
        if link.to_socket == bsdf4.inputs["Base Color"]:
            base_src4 = link.from_node
    check(base_src4 is not None and base_src4.type == "MIX_RGB",
          "mask-only material still builds the player-colour mix chain")
    first_mix = None
    node = base_src4
    while node is not None and node.type == "MIX_RGB":
        first_mix = node
        for link in tree4.links:
            if link.to_socket == node.inputs["Color1"]:
                node = link.from_node
                break
        else:
            node = None
    check(node is not None and node.type == "RGB",
          "bottom of the mix chain is fed by the white placeholder node, "
          "actually linked in (not just a bare default value)")

    mask_sep4 = None
    for link in tree4.links:
        if link.to_socket == first_mix.inputs["Fac"]:
            mask_sep4 = link.from_node
    check(mask_sep4 is not None,
          "mask-only material's Separate Color node feeds the mix factor")
    if mask_sep4 is not None:
        src = None
        for link in tree4.links:
            if link.to_socket == mask_sep4.inputs["Color"]:
                src = link.from_node
        check(src is not None and src.name == "Mask",
              "mask-only material's Separate Color is fed by the mask "
              "texture too")

    # --- Layout sanity again for the mask-only (no base texture) case. ---
    step = 300
    locations4 = [(round(n.location.x), round(n.location.y))
                 for n in tree4.nodes]
    check(all(x % step == 0 and y % step == 0 for x, y in locations4),
          "mask-only material also stays on the 300-unit grid")
    check(len(locations4) == len(set(locations4)),
          "mask-only material has no overlapping node locations")


def error_case(tmpdir):
    print("\n=== Error handling ===")
    reset_scene()
    bad = os.path.join(tmpdir, "bad.rigid_model_v2")
    with open(bad, "wb") as handle:
        handle.write(b"JUNKJUNKJUNK" + b"\0" * 200)
    try:
        import_rmv2.import_file(bpy.context, bad, {})
        check(False, "bad magic raises RmvFormatError")
    except rf.RmvFormatError:
        check(True, "bad magic raises RmvFormatError")

    try:
        export_rmv2.export_file(bpy.context, os.path.join(
            tmpdir, "empty.rigid_model_v2"), {"source": "SELECTED",
                                              "version": "7"})
        check(False, "empty export raises ExportError")
    except export_rmv2.ExportError:
        check(True, "empty export raises ExportError")


def operator_case(tmpdir):
    """Exercise the actual operators (register + invoke through bpy.ops)."""
    print("\n=== Operators ===")
    reset_scene()
    src = os.path.join(tmpdir, "op_src.rigid_model_v2")
    with open(src, "wb") as handle:
        handle.write(rf.save(make_file(8, rf.VF_CINEMATIC)))

    result = bpy.ops.import_scene.rmv2(filepath=src, import_all_lods=True)
    check(result == {"FINISHED"}, "import operator finished")
    check(any(c.rmv2.is_rmv2_root for c in bpy.data.collections),
          "operator created rmv2 root collection")

    # default (import_all_lods off) only brings in the best LOD
    reset_scene()
    result = bpy.ops.import_scene.rmv2(filepath=src)
    check(result == {"FINISHED"}, "default import finished")
    root_default = next(c for c in bpy.data.collections
                        if c.rmv2.is_rmv2_root)
    check(len([c for c in root_default.children if c.rmv2.is_lod]) == 1,
          "default import only brings in the most detailed LOD")
    reset_scene()
    result = bpy.ops.import_scene.rmv2(filepath=src, import_all_lods=True)

    dst = os.path.join(tmpdir, "op_dst.rigid_model_v2")
    root = next(c for c in bpy.data.collections if c.rmv2.is_rmv2_root)
    activate_collection(root.name)
    result = bpy.ops.export_scene.rmv2(filepath=dst, source="AUTO",
                                       version="8")
    check(result == {"FINISHED"}, "export operator finished")
    with open(dst, "rb") as handle:
        out = rf.load(handle.read())
    check(len(out.lods) == 2, "operator export kept lods")


# ---------------------------------------------------------------------------
# .anim tests
# ---------------------------------------------------------------------------

from mathutils import Matrix, Quaternion, Vector  # noqa: E402

# 4-bone skeleton in game space (right-handed, Y-up). Parent-relative
# translations + xyzw rotations; spine_0 is rotated so quaternion/space
# conversion bugs cannot hide.
ANIM_BONES = [af.AnimBone("root", -1), af.AnimBone("spine_0", 0),
              af.AnimBone("spine_1", 1), af.AnimBone("arm_left", 1)]
_SQ2 = 0.7071068
BIND_T = [(0.0, 1.0, 0.0), (0.0, 0.5, 0.0),
          (0.0, 0.5, 0.0), (0.3, 0.2, -0.1)]
BIND_R = [(0.0, 0.0, 0.0, 1.0), (_SQ2, 0.0, 0.0, _SQ2),
          (0.0, 0.0, 0.0, 1.0), (0.0, _SQ2, 0.0, _SQ2)]


def make_skeleton_anim():
    # Real bind-pose skeleton files carry 2-3 identical frames - they are
    # structurally indistinguishable from a short animation.
    return af.build_simple(
        7, "humanoid01", 20.0, ANIM_BONES,
        np.array([BIND_T, BIND_T], np.float32),
        np.array([BIND_R, BIND_R], np.float32))


def make_animation_anim(frames=4):
    translations = np.zeros((frames, 4, 3), np.float32)
    rotations = np.zeros((frames, 4, 4), np.float32)
    for f in range(frames):
        for b in range(4):
            x, y, z = BIND_T[b]
            translations[f, b] = (x, y + 0.05 * f * (b == 0), z)
            angle = 0.25 * f * (1 + b % 2)
            axis = Vector((0.0, 1.0, 0.0) if b % 2 else (1.0, 0.0, 0.0))
            extra = Quaternion(axis, angle)
            base = Quaternion((BIND_R[b][3], *BIND_R[b][:3]))
            q = base @ extra
            rotations[f, b] = (q.x, q.y, q.z, q.w)
    return af.build_simple(7, "humanoid01", 20.0, ANIM_BONES,
                           translations, rotations,
                           flags=["shake_camera"])


def make_realistic_anim(frames=3):
    """v7 animation exercising every mapping kind: bones 0/1 dynamic,
    bone 2 constant via the static frame, bone 3 not animated at all
    (falls back to the skeleton's bind pose, like real game files)."""
    anim = af.AnimFile(version=7, frame_rate=20.0,
                       skeleton_name="humanoid01",
                       flags=["dismember_head"])
    anim.bones = list(ANIM_BONES)
    anim.duration = (frames - 1) / 20.0
    part = af.AnimPart()
    part.translation_mappings = [af.BoneMapping(v)
                                 for v in (0, 1, 10000, -1)]
    part.rotation_mappings = [af.BoneMapping(v)
                              for v in (0, 1, 10000, -1)]
    part.static_frame = af.AnimFrame(
        translations=np.array([[0.0, 0.45, 0.05]], np.float32),
        rotations=np.array([(0.0, _SQ2, 0.0, _SQ2)], np.float32))
    for f in range(frames):
        t = np.array([[0.0, 1.0 + 0.1 * f, 0.0],
                      [0.1 * f, 0.5, 0.0]], np.float32)
        q0 = Quaternion(Vector((1.0, 0.0, 0.0)), 0.2 * f)
        q1 = Quaternion(Vector((0.0, 1.0, 0.0)), 0.3 * f)
        r = np.array([(q0.x, q0.y, q0.z, q0.w),
                      (q1.x, q1.y, q1.z, q1.w)], np.float32)
        part.dynamic_frames.append(af.AnimFrame(translations=t,
                                                rotations=r))
    anim.parts = [part]
    return anim


def game_world_positions(anim, frame, bind_anim=None):
    """Forward kinematics in game space -> per-bone Blender world position
    via the mesh path's (trusted) coordinate conversion.  Channels the
    file does not animate take the bind pose from `bind_anim`."""
    resolved = af.resolve(anim)
    translations = resolved.translations[frame].copy()
    rotations = resolved.rotations[frame].copy()
    if bind_anim is not None:
        bind = af.resolve(bind_anim)
        for b in range(len(anim.bones)):
            if not resolved.has_translation[b]:
                translations[b] = bind.translations[0][b]
            if not resolved.has_rotation[b]:
                rotations[b] = bind.rotations[0][b]
    world = []
    for i, bone in enumerate(anim.bones):
        t = Vector(translations[i].tolist())
        x, y, z, w = rotations[i].tolist()
        local = Quaternion((w, x, y, z)).to_matrix().to_4x4()
        local.translation = t
        if bone.parent >= 0:
            world.append(world[bone.parent] @ local)
        else:
            world.append(local)
    return [Vector(utils.game_to_blender_v(m.translation))
            for m in world]


def write_anim(tmpdir, name, anim):
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as handle:
        handle.write(af.save(anim))
    return path


def anim_skeleton_case(tmpdir):
    """Import a bind-pose skeleton .anim; verify the armature."""
    print("\n=== .anim skeleton import ===")
    reset_scene()
    path = write_anim(tmpdir, "humanoid01.anim", make_skeleton_anim())

    arm_obj, stats, warnings = import_anim.import_file(
        bpy.context, path, {"mode": "SKELETON"})
    print("  warnings:", warnings or "none")
    check(stats["created_armature"], "created a new armature")
    check(stats["bones"] == 4 and len(arm_obj.data.bones) == 4,
          "4 bones created")
    check(stats["frames"] == 2, "both bind-pose frames read")
    check(arm_obj.animation_data is None
          or arm_obj.animation_data.action is None,
          "no action keyed for a skeleton by default")
    check(arm_obj.data[rmv2_skeleton.SKELETON_NAME_PROP] == "humanoid01",
          "skeleton name stored on the armature")
    check(arm_obj.data.bones["spine_0"].parent.name == "root",
          "parenting follows the file")
    check(arm_obj.data.bones["arm_left"][rmv2_skeleton.BONE_INDEX_PROP] == 3,
          "bone indices stamped as custom properties")

    bpy.context.view_layer.update()
    expected = game_world_positions(make_skeleton_anim(), 0)
    for i, bone in enumerate(ANIM_BONES):
        head = (arm_obj.matrix_world @
                Matrix.Translation(arm_obj.data.bones[bone.name].head_local)
                ).translation
        err = (head - expected[i]).length
        check(err < 1e-5,
              f"bone '{bone.name}' rest head matches game-space FK "
              f"(err {err:.6f})")
    return arm_obj


def anim_flow1_case(tmpdir):
    """RMV2 first, then .anim: groups renamed, meshes attached."""
    print("\n=== Flow 1: RMV2 then .anim ===")
    reset_scene()
    rmv_path = os.path.join(tmpdir, "flow1.rigid_model_v2")
    with open(rmv_path, "wb") as handle:
        handle.write(rf.save(make_file(7, rf.VF_CINEMATIC,
                                       with_attach_points=False)))
    root, _ = import_rmv2.import_file(bpy.context, rmv_path, {})
    meshes = [o for o in root.all_objects if o.type == "MESH"]
    check(len(meshes) == 2, "2 meshes imported (1 per lod)")
    check({g.name for g in meshes[0].vertex_groups} == {"bone_0", "bone_3"},
          "no attach points -> bone_<i> fallback group names")

    # Only lod0 is user-selectable (the importer hides the other LOD
    # collections); the anim import must still cover the whole model.
    lod0_mesh = next(o for o in root.children[0].objects
                     if o.type == "MESH")
    lod0_mesh.select_set(True)
    anim_path = write_anim(tmpdir, "flow1_skel.anim", make_skeleton_anim())
    arm_obj, stats, warnings = import_anim.import_file(
        bpy.context, anim_path, {"mode": "SKELETON"})
    print("  warnings:", warnings or "none")
    check(stats["created_armature"], "new armature created")
    check(arm_obj.name in {o.name for o in root.all_objects},
          "armature linked into the RMV2 root collection")
    check(stats["attached"] == 2,
          f"selection expanded to the hidden LOD ({stats['attached']})")
    check(stats["renamed_groups"] == 4, "2 groups renamed on each mesh "
          f"({stats['renamed_groups']})")
    for obj in meshes:
        names = {g.name for g in obj.vertex_groups}
        check(names == {"root", "arm_left"},
              f"{obj.name}: groups renamed to bone names ({names})")
        mods = [m for m in obj.modifiers if m.type == "ARMATURE"]
        check(len(mods) == 1 and mods[0].object is arm_obj,
              f"{obj.name}: armature modifier set")
        check(obj.parent is arm_obj, f"{obj.name}: parented to armature")

    # meshes must not have moved
    bpy.context.view_layer.update()
    pivot_b = utils.game_to_blender_v((0.25, 1.5, -0.75))
    check(np.allclose(meshes[0].matrix_world.translation, pivot_b,
                      atol=1e-5), "mesh world position untouched by attach")


def anim_flow2_case(tmpdir):
    """.anim first, then RMV2: groups named from the armature's bones."""
    print("\n=== Flow 2: .anim then RMV2 ===")
    reset_scene()
    anim_path = write_anim(tmpdir, "flow2_skel.anim", make_skeleton_anim())
    arm_obj, _, _ = import_anim.import_file(bpy.context, anim_path,
                                            {"mode": "SKELETON"})
    check(arm_obj.select_get() and bpy.context.view_layer.objects.active
          is arm_obj, "armature selected + active after import")

    rmv_path = os.path.join(tmpdir, "flow2.rigid_model_v2")
    with open(rmv_path, "wb") as handle:
        handle.write(rf.save(make_file(7, rf.VF_CINEMATIC,
                                       with_attach_points=False)))
    root, stats = import_rmv2.import_file(bpy.context, rmv_path, {})
    meshes = [o for o in root.all_objects if o.type == "MESH"]
    for obj in meshes:
        names = {g.name for g in obj.vertex_groups}
        check(names == {"root", "arm_left"},
              f"{obj.name}: groups named from armature bones ({names})")
        mods = [m for m in obj.modifiers if m.type == "ARMATURE"]
        check(len(mods) == 1 and mods[0].object is arm_obj,
              f"{obj.name}: armature modifier set")
        check(obj.parent is arm_obj, f"{obj.name}: parented to armature")

    # Re-export the model: weights must map back to the file bone indices
    # through the armature (no attach points stored on this collection).
    activate_collection(root.name)
    dst = os.path.join(tmpdir, "flow2_out.rigid_model_v2")
    stats, warnings = export_rmv2.export_file(bpy.context, dst, {
        "source": "AUTO", "version": "7", "skeleton_name": "",
        "apply_modifiers": True, "high_precision": True,
        "write_attach_points": True, "global_scale": 1.0})
    print("  export warnings:", warnings or "none")
    with open(dst, "rb") as handle:
        result = rf.load(handle.read())
    model = result.lods[0].models[0]
    aps = [(a.name, a.bone_index) for a in model.material.attachment_points]
    check(aps == [("root", 0), ("spine_0", 1), ("spine_1", 2),
                  ("arm_left", 3)],
          f"attachment points generated in armature bone order ({aps})")
    pairs = {(int(b), round(float(w), 2))
             for b, w in zip(model.mesh.bone_indices[0],
                             model.mesh.bone_weights[0]) if w > 0}
    check(pairs == {(0, 0.7), (3, 0.3)},
          f"weights land on the original bone indices ({pairs})")


def anim_animation_case(tmpdir):
    """Skeleton, then multi-frame animation onto it; pose sampled per
    frame must match game-space FK; then export and compare to source."""
    print("\n=== .anim animation apply + export roundtrip ===")
    reset_scene()
    skel_path = write_anim(tmpdir, "anim_skel.anim", make_skeleton_anim())
    arm_obj, _, _ = import_anim.import_file(bpy.context, skel_path,
                                            {"mode": "SKELETON"})

    animation = make_animation_anim(frames=4)
    anim_path = write_anim(tmpdir, "anim_run.anim", animation)
    arm2, stats, warnings = import_anim.import_file(
        bpy.context, anim_path, {"mode": "ANIMATION"})
    print("  warnings:", warnings or "none")
    check(arm2 is arm_obj, "animation applied to the selected armature")
    check(not stats["created_armature"], "no second armature created")
    check(stats["keyed_bones"] == 4, f"keys on all 4 bones "
          f"({stats['keyed_bones']})")
    check(bpy.context.scene.frame_start == 0
          and bpy.context.scene.frame_end == 3, "scene frame range set")
    fps = (bpy.context.scene.render.fps
           / bpy.context.scene.render.fps_base)
    check(abs(fps - 20.0) < 1e-4, f"scene fps set to 20 ({fps})")
    action = arm_obj.animation_data.action
    check(action is not None, "action assigned")

    for frame in (0, 2, 3):
        bpy.context.scene.frame_set(frame)
        expected = game_world_positions(animation, frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        arm_eval = arm_obj.evaluated_get(depsgraph)
        worst = 0.0
        for i, bone in enumerate(ANIM_BONES):
            pos = (arm_eval.matrix_world
                   @ arm_eval.pose.bones[bone.name].matrix).translation
            worst = max(worst, (pos - expected[i]).length)
        check(worst < 2e-3,
              f"frame {frame}: posed bone positions match game-space FK "
              f"(max err {worst:.5f})")

    # ---- export the action back out ----
    out_path = os.path.join(tmpdir, "anim_out.anim")
    stats, warnings = export_anim.export_file(bpy.context, out_path, {
        "mode": "ANIMATION", "version": "7", "skeleton_name": "",
        "frame_rate": 0.0, "global_scale": 1.0})
    print("  export warnings:", warnings or "none")
    check(stats["frames"] == 4 and stats["bones"] == 4,
          "exported 4 frames x 4 bones")
    with open(out_path, "rb") as handle:
        out = af.load(handle.read())
    check(out.skeleton_name == "humanoid01",
          "skeleton name taken from the armature")
    check(abs(out.frame_rate - 20.0) < 1e-4, "frame rate preserved")
    check(out.flags == ["shake_camera"], f"v7 flags preserved ({out.flags})")
    check([(b.name, b.parent) for b in out.bones]
          == [(b.name, b.parent) for b in ANIM_BONES],
          "bone table preserved")

    res_in = af.resolve(animation)
    res_out = af.resolve(out)
    t_err = np.abs(res_in.translations - res_out.translations).max()
    check(t_err < 1e-4, f"translations roundtrip (max err {t_err:.6f})")
    q_in, q_out = res_in.rotations, res_out.rotations
    q_err = np.minimum(np.abs(q_in - q_out).max(axis=-1),
                       np.abs(q_in + q_out).max(axis=-1)).max()
    check(q_err < 2e-3, f"rotations roundtrip (max err {q_err:.6f})")

    # ---- bind pose export ----
    bind_path = os.path.join(tmpdir, "bind_out.anim")
    stats, _ = export_anim.export_file(bpy.context, bind_path, {
        "mode": "BINDPOSE", "version": "7", "skeleton_name": "",
        "frame_rate": 0.0, "global_scale": 1.0})
    check(stats["frames"] == 2,
          "bind pose export writes two identical frames (vanilla layout)")
    with open(bind_path, "rb") as handle:
        bind = af.load(handle.read())
    check(abs(bind.duration - 0.1) < 1e-6,
          f"bind pose duration is 0.1s like vanilla ({bind.duration})")
    res_bind = af.resolve(bind)
    skel = af.resolve(make_skeleton_anim())
    t_err = np.abs(res_bind.translations[0] - skel.translations[0]).max()
    q_err = np.minimum(
        np.abs(res_bind.rotations[0] - skel.rotations[0]).max(axis=-1),
        np.abs(res_bind.rotations[0] + skel.rotations[0]).max(axis=-1)).max()
    check(t_err < 1e-4 and q_err < 2e-3,
          f"bind pose matches the source skeleton (t {t_err:.6f}, "
          f"q {q_err:.6f})")


def anim_mode_gating_case(tmpdir):
    """The exact reported flow: activate the root collection, import the
    bind skeleton, then select ONE MESH and import a real animation
    (static frame + un-animated bones, like game files). Must apply to
    the existing armature - never a second one - and the armature must
    live in the root collection."""
    print("\n=== Skeleton/Animation mode gating (user flow) ===")
    reset_scene()
    rmv_path = os.path.join(tmpdir, "gating.rigid_model_v2")
    with open(rmv_path, "wb") as handle:
        handle.write(rf.save(make_file(7, rf.VF_CINEMATIC,
                                       with_attach_points=False)))
    root, _ = import_rmv2.import_file(bpy.context, rmv_path, {})
    activate_collection(root.name)

    # nothing selected, root collection active -> skeleton import
    skel_path = write_anim(tmpdir, "gating_skel.anim", make_skeleton_anim())
    arm_obj, stats, warnings = import_anim.import_file(
        bpy.context, skel_path, {"mode": "SKELETON"})
    print("  skeleton warnings:", warnings or "none")
    check(stats["created_armature"] and stats["attached"] == 2,
          "skeleton import from active collection attached both meshes")
    check(arm_obj.name in {o.name for o in root.all_objects},
          "armature linked into the RMV2 root collection")

    # select one mesh (the reported error path) -> animation import
    bpy.ops.object.select_all(action="DESELECT")
    mesh = next(o for o in root.children[0].objects if o.type == "MESH")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    animation = make_realistic_anim(frames=3)
    anim_path = write_anim(tmpdir, "gating_attack.anim", animation)
    arm2, stats, warnings = import_anim.import_file(
        bpy.context, anim_path, {"mode": "ANIMATION"})
    print("  animation warnings:", warnings or "none")
    check(arm2 is arm_obj, "animation found the armature via the mesh")
    check(not stats["created_armature"], "no armature created")
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    check(len(armatures) == 1, f"exactly one armature in the scene "
          f"({len(armatures)})")
    check(stats["keyed_bones"] == 3,
          f"dynamic + static bones keyed, unanimated bone left alone "
          f"({stats['keyed_bones']})")

    # posed positions must match FK with bind-pose fallback for bone 3
    for frame in (0, 2):
        bpy.context.scene.frame_set(frame)
        expected = game_world_positions(animation, frame,
                                        bind_anim=make_skeleton_anim())
        depsgraph = bpy.context.evaluated_depsgraph_get()
        arm_eval = arm_obj.evaluated_get(depsgraph)
        worst = max(
            ((arm_eval.matrix_world
              @ arm_eval.pose.bones[b.name].matrix).translation
             - expected[i]).length
            for i, b in enumerate(ANIM_BONES))
        check(worst < 2e-3,
              f"frame {frame}: pose matches FK incl. static/bind bones "
              f"(max err {worst:.5f})")

    # a second animation still reuses the same armature, new action
    first_action = arm_obj.animation_data.action
    anim_path2 = write_anim(tmpdir, "gating_idle.anim",
                            make_animation_anim(frames=2))
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    arm3, stats, _ = import_anim.import_file(
        bpy.context, anim_path2, {"mode": "ANIMATION"})
    check(arm3 is arm_obj and len(
        [o for o in bpy.data.objects if o.type == "ARMATURE"]) == 1,
        "second animation import still uses the one armature")
    check(arm_obj.animation_data.action is not first_action,
          "second animation became the active action")

    # gating errors
    try:
        import_anim.import_file(bpy.context, skel_path,
                                {"mode": "SKELETON"})
        check(False, "skeleton import onto armatured model raises")
    except import_anim.AnimImportError as exc:
        check("already has an armature" in str(exc),
              "skeleton import onto armatured model raises")

    reset_scene()
    root2, _ = import_rmv2.import_file(bpy.context, rmv_path, {})
    activate_collection(root2.name)
    try:
        import_anim.import_file(bpy.context, anim_path,
                                {"mode": "ANIMATION"})
        check(False, "animation import without armature raises")
    except import_anim.AnimImportError as exc:
        check("Import the model's skeleton first" in str(exc),
              "animation import without armature raises")


def anim_operator_case(tmpdir):
    print("\n=== .anim operators ===")
    reset_scene()
    path = write_anim(tmpdir, "op_skel.anim", make_skeleton_anim())
    result = bpy.ops.import_scene.tw_skeleton(filepath=path)
    check(result == {"FINISHED"}, "skeleton import operator finished")
    arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    check(arm is not None and len(arm.data.bones) == 4,
          "operator created the armature")

    anim_path = write_anim(tmpdir, "op_run.anim",
                           make_animation_anim(frames=4))
    result = bpy.ops.import_scene.tw_anim(filepath=anim_path)
    check(result == {"FINISHED"}, "animation import operator finished")
    check(arm.animation_data is not None
          and arm.animation_data.action is not None,
          "operator keyed the action onto the armature")

    dst = os.path.join(tmpdir, "op_out.anim")
    result = bpy.ops.export_scene.tw_anim(filepath=dst, mode="BINDPOSE")
    check(result == {"FINISHED"}, "anim export operator finished")
    with open(dst, "rb") as handle:
        out = af.load(handle.read())
    check(out.bone_count == 4, "operator export kept the bones")

    bad = os.path.join(tmpdir, "bad.anim")
    with open(bad, "wb") as handle:
        handle.write(b"\xff" * 64)
    try:
        result = bpy.ops.import_scene.tw_anim(filepath=bad)
        cancelled = result == {"CANCELLED"}
    except RuntimeError as exc:   # bpy.ops re-raises reported errors
        cancelled = "Unsupported .anim version" in str(exc)
    check(cancelled, "bad .anim cancels cleanly with a format error")


def main():
    print(f"Blender {bpy.app.version_string}")
    io_scene_rmv2.register()
    tmpdir = tempfile.mkdtemp(prefix="rmv2_test_")
    print("tmpdir:", tmpdir)

    try:
        roundtrip_case(tmpdir, 7, rf.VF_CINEMATIC, "cinematic")
        roundtrip_case(tmpdir, 8, rf.VF_CINEMATIC, "cinematic_v8")
        roundtrip_case(tmpdir, 7, rf.VF_WEIGHTED, "weighted")
        roundtrip_case(tmpdir, 6, rf.VF_STATIC, "static")
        roundtrip_case(tmpdir, 8, rf.VF_STATIC, "static_v8")
        native_export_case(tmpdir)
        auto_lod_case(tmpdir)
        skinned_native_case(tmpdir)
        material_node_case()
        error_case(tmpdir)
        operator_case(tmpdir)
        anim_skeleton_case(tmpdir)
        anim_flow1_case(tmpdir)
        anim_flow2_case(tmpdir)
        anim_animation_case(tmpdir)
        anim_mode_gating_case(tmpdir)
        anim_operator_case(tmpdir)
    except Exception:
        traceback.print_exc()
        FAILURES.append("unhandled exception (see traceback)")

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s):")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("ALL BLENDER ROUNDTRIP TESTS PASSED")
    sys.exit(0)


main()
