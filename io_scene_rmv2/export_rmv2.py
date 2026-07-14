"""Exporter: Blender objects/collections -> .rigid_model_v2.

Pipeline per mesh object:
  evaluated mesh (modifiers applied, armature deform excluded)
  -> triangulated via loop triangles (non-destructive)
  -> per-loop position/normal/tangent/uv/colour gathered with numpy
  -> rotation/scale of the world transform baked in (the translation is
     written as the material pivot; the game adds it back at render time),
     then converted to game space
  -> quantized to file precision and welded into unique vertices
  -> bone weights resolved from vertex groups (attachment-point names,
     bone_<i> names, or armature bone order)
"""

from __future__ import annotations

import json
import re

import bpy
import numpy as np

from . import rmv2_format as rf
from . import skeleton
from . import utils
from .properties import VERTEX_FORMAT_TO_INT

_BONE_PATTERN = re.compile(r"^bone_(\d+)$")


class ExportError(Exception):
    pass


# ---------------------------------------------------------------------------
# Collection / LOD gathering
# ---------------------------------------------------------------------------

_LOD_NAME_PATTERN = re.compile(r"lod\s*_?(\d+)", re.IGNORECASE)


def _mesh_objects(collection, recursive=True):
    objs = [o for o in collection.objects if o.type == "MESH"]
    if recursive:
        for child in collection.children:
            if child.rmv2.is_lod:
                continue
            objs += _mesh_objects(child)
    # preserve order, drop duplicates
    seen = set()
    out = []
    for o in objs:
        if o.name not in seen:
            seen.add(o.name)
            out.append(o)
    return out


def _find_parent_collection(scene, target):
    stack = [scene.collection]
    while stack:
        col = stack.pop()
        for child in col.children:
            if child == target:
                return col
            stack.append(child)
    return None


def _lod_children(root):
    """Child collections that look like LODs, sorted by level."""
    lods = []
    for child in root.children:
        level = None
        if child.rmv2.is_lod:
            level = child.rmv2.lod_level
        else:
            m = _LOD_NAME_PATTERN.search(child.name)
            if m:
                level = int(m.group(1))
        if level is not None:
            lods.append((level, child))
    lods.sort(key=lambda item: (item[0], item[1].name))
    return [c for _, c in lods]


def default_camera_distance(index: int) -> float:
    return float(20 * (2 ** index))


def gather_lods(context, options):
    """Returns (root_collection_or_None, [ (lod_info, [objects]) ])."""
    source = options.get("source", "AUTO")
    scene = context.scene

    root = None
    active = context.view_layer.active_layer_collection
    active_col = active.collection if active else None
    if active_col is not None:
        if active_col.rmv2.is_lod:
            parent = _find_parent_collection(scene, active_col)
            if parent is not None:
                active_col = parent
        if active_col.rmv2.is_rmv2_root or _lod_children(active_col):
            root = active_col
        elif source in ("AUTO", "COLLECTION"):
            root = active_col if active_col != scene.collection else None

    lods = []
    if source in ("AUTO", "COLLECTION") and root is not None:
        children = _lod_children(root)
        if children:
            for i, col in enumerate(children):
                s = col.rmv2
                info = {
                    "level": s.lod_level if s.is_lod else i,
                    "camera_distance": (s.camera_distance if s.is_lod
                                        else default_camera_distance(i)),
                    "quality_level": s.quality_level if s.is_lod else 0,
                }
                lods.append((info, [o for o in col.objects
                                    if o.type == "MESH"]))
        else:
            lods.append((None, _mesh_objects(root)))
    if not lods:
        if source == "VISIBLE":
            objs = [o for o in context.visible_objects if o.type == "MESH"]
        else:
            objs = [o for o in context.selected_objects if o.type == "MESH"]
            if not objs and source == "AUTO":
                objs = [o for o in context.visible_objects
                        if o.type == "MESH"]
        lods.append((None, objs))

    lods = [(info if info is not None else {
        "level": i, "camera_distance": default_camera_distance(i),
        "quality_level": 0}, objs)
        for i, (info, objs) in enumerate(lods)]

    return root, lods


def expand_auto_lods(lods, count: int):
    """Turn a single LOD into `count` LODs; LOD i>0 tags its objects with
    a decimate ratio of 0.5^i (applied at mesh extraction time)."""
    info0, objs = lods[0]
    out = []
    for i in range(count):
        info = dict(info0) if i == 0 else {
            "level": i,
            "camera_distance": default_camera_distance(i),
            "quality_level": 0,
        }
        info["level"] = i
        info["decimate_ratio"] = 0.5 ** i
        out.append((info, objs))
    return out


# ---------------------------------------------------------------------------
# Bones / attachment points
# ---------------------------------------------------------------------------

def _find_armature(obj):
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None:
            return mod.object
    parent = obj.parent
    if parent is not None and parent.type == "ARMATURE":
        return parent
    return None


def load_attach_points(root_collection, objects):
    """Attachment points from the root collection's stored list, else
    generated from the first armature found (bone order = bone index,
    'bn_' stripped)."""
    if root_collection is not None and root_collection.rmv2.attach_points:
        return [rf.RmvAttachmentPoint(
            name=entry.name,
            bone_index=int(entry.bone_index),
            matrix=tuple(float(v) for v in entry.matrix))
            for entry in root_collection.rmv2.attach_points]
    for obj in objects:
        armature = _find_armature(obj)
        if armature is not None:
            return [rf.RmvAttachmentPoint(
                name=(b.name[3:] if b.name.startswith("bn_") else b.name),
                bone_index=i, matrix=rf.IDENTITY_3X4)
                for i, b in skeleton.bone_index_pairs(armature)]
    return []


def resolve_bone_map(obj, attach_names: dict, warnings: list) -> dict:
    """vertex-group index -> game bone index."""
    armature = _find_armature(obj)
    bone_order = {}
    if armature is not None:
        for i, bone in skeleton.bone_index_pairs(armature):
            bone_order[bone.name] = i
            if bone.name.startswith("bn_"):
                bone_order.setdefault(bone.name[3:], i)

    mapping = {}
    unmapped = []
    for gi, group in enumerate(obj.vertex_groups):
        name = group.name
        stripped = name[3:] if name.startswith("bn_") else name
        match = _BONE_PATTERN.match(name)
        if name in attach_names:
            mapping[gi] = attach_names[name]
        elif stripped in attach_names:
            mapping[gi] = attach_names[stripped]
        elif match:
            mapping[gi] = int(match.group(1))
        elif name in bone_order:
            mapping[gi] = bone_order[name]
        elif stripped in bone_order:
            mapping[gi] = bone_order[stripped]
        else:
            unmapped.append(name)

    if unmapped and mapping:
        warnings.append(
            f"{obj.name}: ignored vertex groups with no matching bone: "
            + ", ".join(unmapped[:8])
            + ("..." if len(unmapped) > 8 else ""))
    elif unmapped and not mapping:
        warnings.append(
            f"{obj.name}: none of its vertex groups could be mapped to bone "
            "indices (no attachment points, no armature, no bone_<i> names) "
            "- exporting as static")
    bad = {gi: b for gi, b in mapping.items() if not 0 <= b <= 255}
    if bad:
        warnings.append(f"{obj.name}: bone indices above 255 are not "
                        "representable; affected groups ignored")
        for gi in bad:
            del mapping[gi]
    return mapping


def _gather_weights(me, bone_map: dict, k: int, obj_name: str,
                    warnings: list):
    """Per-vertex (n,k) bone index/weight arrays from vertex groups."""
    n = len(me.vertices)
    indices = np.zeros((n, k), np.uint8)
    weights = np.zeros((n, k), np.float32)
    truncated = 0
    unweighted = 0
    for i, vert in enumerate(me.vertices):
        infl = [(bone_map[g.group], g.weight) for g in vert.groups
                if g.group in bone_map and g.weight > 0.0]
        if not infl:
            unweighted += 1
            weights[i, 0] = 1.0
            continue
        infl.sort(key=lambda item: item[1], reverse=True)
        if len(infl) > k:
            truncated += 1
            infl = infl[:k]
        total = sum(w for _, w in infl)
        for slot, (bone, weight) in enumerate(infl):
            indices[i, slot] = bone
            weights[i, slot] = weight / total
    if truncated:
        warnings.append(
            f"{obj_name}: {truncated} vertices had more than {k} bone "
            f"influences; lowest weights dropped and renormalized")
    if unweighted:
        warnings.append(
            f"{obj_name}: {unweighted} vertices had no bone weights; "
            "assigned fully to bone 0")
    return indices, weights


# ---------------------------------------------------------------------------
# Mesh extraction
# ---------------------------------------------------------------------------

def _get_corner_normals(me):
    nl = len(me.loops)
    arr = np.empty(nl * 3, np.float32)
    if hasattr(me, "corner_normals"):           # Blender 4.1+
        me.corner_normals.foreach_get("vector", arr)
    else:
        me.calc_normals_split()
        me.loops.foreach_get("normal", arr)
    return arr.reshape(nl, 3)


def _get_uv_layer(me, index: int):
    if index < len(me.uv_layers):
        nl = len(me.loops)
        arr = np.empty(nl * 2, np.float32)
        me.uv_layers[index].data.foreach_get("uv", arr)
        return arr.reshape(nl, 2)
    return None


def _get_colours(me, loop_vidx: np.ndarray):
    """Per-loop RGBA in raw byte space ([0,1] float)."""
    ca = None
    if me.color_attributes:
        ca = me.color_attributes.get("Colour") \
            or me.color_attributes.active_color \
            or me.color_attributes[0]
    if ca is None:
        return None
    count = len(ca.data)
    arr = np.empty(count * 4, np.float32)
    if ca.data_type == "BYTE_COLOR":
        try:
            ca.data.foreach_get("color_srgb", arr)
        except (AttributeError, TypeError):
            ca.data.foreach_get("color", arr)
    else:
        ca.data.foreach_get("color", arr)
    arr = np.clip(arr.reshape(count, 4), 0.0, 1.0)
    if ca.domain == "POINT":
        return arr[loop_vidx]
    if ca.domain == "CORNER":
        return arr
    return None


def _fallback_tangents(positions, uvs, normals, tri_loops, loop_vidx):
    """Per-loop tangents computed from triangle UVs (used when Blender's
    calc_tangents is unavailable, e.g. ngons present)."""
    nl = len(loop_vidx)
    tangents = np.zeros((nl, 3), np.float32)
    signs = np.ones(nl, np.float32)
    tris = tri_loops.reshape(-1, 3)
    p = positions[loop_vidx[tris]]           # (t,3verts,3)
    t_uv = uvs[tris]                          # (t,3verts,2)
    e1 = p[:, 1] - p[:, 0]
    e2 = p[:, 2] - p[:, 0]
    d1 = t_uv[:, 1] - t_uv[:, 0]
    d2 = t_uv[:, 2] - t_uv[:, 0]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    det = np.where(np.abs(det) < 1e-12, 1.0, det)
    tan = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None]
    for corner in range(3):
        tangents[tris[:, corner]] = tan
    # orthonormalize against the normal
    dot = (tangents * normals).sum(axis=1, keepdims=True)
    tangents = tangents - normals * dot
    return utils.normalize_rows(tangents, fallback=(1.0, 0.0, 0.0)), signs


def extract_mesh_arrays(context, obj, options, warnings,
                        decimate_ratio=1.0):
    """Evaluated, triangulated per-loop arrays (Blender space), with the
    rotation/scale of the world transform applied but not its translation
    (positions are pivot-relative, matching the file format).

    A decimate_ratio < 1 appends a temporary collapse-Decimate modifier
    (auto-LOD generation). Returns dict or None if the mesh has no faces."""
    apply_modifiers = options.get("apply_modifiers", True) \
        or decimate_ratio < 1.0

    disabled = []
    temp_mod = None
    if apply_modifiers:
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.show_viewport:
                mod.show_viewport = False
                disabled.append(mod)
    if decimate_ratio < 1.0:
        temp_mod = obj.modifiers.new("__rmv2_auto_lod", "DECIMATE")
        temp_mod.decimate_type = "COLLAPSE"
        temp_mod.ratio = decimate_ratio
        # Collapsing straight to triangles keeps the silhouette better at
        # low ratios, and the exporter triangulates anyway.
        temp_mod.use_collapse_triangulate = True
    try:
        depsgraph = context.evaluated_depsgraph_get()
        source = obj.evaluated_get(depsgraph) if apply_modifiers else obj
        me = source.to_mesh(preserve_all_data_layers=True,
                            depsgraph=depsgraph)

        me.calc_loop_triangles()
        ntris = len(me.loop_triangles)
        if ntris == 0:
            source.to_mesh_clear()
            return None

        nl = len(me.loops)
        nv = len(me.vertices)

        loop_vidx = np.empty(nl, np.int32)
        me.loops.foreach_get("vertex_index", loop_vidx)
        positions = np.empty(nv * 3, np.float32)
        me.vertices.foreach_get("co", positions)
        positions = positions.reshape(nv, 3)

        uv0 = _get_uv_layer(me, 0)
        uv1 = _get_uv_layer(me, 1)
        if uv0 is None:
            uv0 = np.zeros((nl, 2), np.float32)
            warnings.append(f"{obj.name}: no UV map; exporting zero UVs")

        tri_loops = np.empty(ntris * 3, np.int32)
        me.loop_triangles.foreach_get("loops", tri_loops)

        # Tangent space
        tangents = None
        signs = None
        if len(me.uv_layers):
            try:
                me.calc_tangents(uvmap=me.uv_layers[0].name)
                tangents = np.empty(nl * 3, np.float32)
                me.loops.foreach_get("tangent", tangents)
                tangents = tangents.reshape(nl, 3)
                signs = np.empty(nl, np.float32)
                me.loops.foreach_get("bitangent_sign", signs)
            except RuntimeError:
                tangents = None

        normals = _get_corner_normals(me)

        if tangents is None:
            tangents, signs = _fallback_tangents(
                positions, uv0, normals, tri_loops, loop_vidx)

        colours = _get_colours(me, loop_vidx)
        if colours is None:
            colours = np.zeros((nl, 4), np.float32)
            colours[:, 3] = 1.0

        # Per-vertex weights (indices into obj.vertex_groups resolved later)
        weight_source_me = me

        # World transform. Positions get only the rotation/scale part:
        # the translation is written as the material pivot instead, and
        # the game adds it back at render time (raw + pivot), so baking
        # it into the vertices would apply it twice.
        mw = np.array(obj.matrix_world, dtype=np.float64)
        m3 = mw[:3, :3]
        positions_w = positions @ m3.T
        det = float(np.linalg.det(m3))
        if abs(det) < 1e-12:
            source.to_mesh_clear()
            warnings.append(f"{obj.name}: degenerate (zero scale) transform;"
                            " skipped")
            return None
        nmat = np.linalg.inv(m3).T
        normals_w = utils.normalize_rows(normals @ nmat.T)
        tangents_w = utils.normalize_rows(tangents @ m3.T,
                                          fallback=(1.0, 0.0, 0.0))
        binormals_w = np.cross(normals_w, tangents_w) * signs[:, None]
        binormals_w = utils.normalize_rows(binormals_w,
                                           fallback=(0.0, 1.0, 0.0))

        if det < 0:
            warnings.append(
                f"{obj.name}: negative scale (mirrored) transform; consider "
                "applying scale. Winding compensated automatically")

        result = {
            "loop_vidx": loop_vidx,
            "positions_pivot_rel": positions_w.astype(np.float32),
            "normals": normals_w,
            "tangents": tangents_w,
            "binormals": binormals_w,
            "uv0": uv0,
            "uv1": uv1,
            "colours": colours,
            "tri_loops": tri_loops,
            "mirrored": det < 0,
            "mesh_for_weights": weight_source_me,
            "source_object": source,
        }
        return result
    finally:
        if temp_mod is not None:
            obj.modifiers.remove(temp_mod)
        for mod in disabled:
            mod.show_viewport = True


# ---------------------------------------------------------------------------
# Model building
# ---------------------------------------------------------------------------

def _resolve_vertex_format(settings, bone_map, me, warnings, obj_name):
    choice = settings.vertex_format
    if choice != "AUTO":
        fmt = VERTEX_FORMAT_TO_INT[choice]
        if fmt != rf.VF_STATIC and not bone_map:
            warnings.append(
                f"{obj_name}: {choice.lower()} format requested but no "
                "bone-mapped vertex groups found; exporting as Static")
            return rf.VF_STATIC
        return fmt
    if not bone_map:
        return rf.VF_STATIC
    has_weights = any(
        g.group in bone_map and g.weight > 0.0
        for v in me.vertices for g in v.groups)
    return rf.VF_CINEMATIC if has_weights else rf.VF_STATIC


def _resolve_material_id(settings, fmt: int) -> int:
    if settings.material_id == "AUTO":
        return rf.MAT_DEFAULT if fmt == rf.VF_STATIC else rf.MAT_WEIGHTED
    if settings.material_id == "OTHER":
        return settings.material_id_raw
    return rf.MATERIAL_IDS.get(settings.material_id, rf.MAT_DEFAULT)


def _material_from_settings(obj, settings, fmt: int, scale: float,
                            attach_points, options) -> rf.WeightedMaterial:
    extra = {}
    if settings.extra_json:
        try:
            extra = json.loads(settings.extra_json)
        except ValueError:
            extra = {}

    pivot_b = obj.matrix_world.translation
    mat = rf.WeightedMaterial(
        material_id=_resolve_material_id(settings, fmt),
        vertex_format=fmt,
        model_name=(settings.model_name
                    or utils.strip_blender_suffix(obj.name))[:31],
        texture_directory=settings.texture_directory,
        filters=settings.filters,
        pivot=tuple(v * scale for v in utils.blender_to_game_v(pivot_b)),
        matrix_index=settings.matrix_index,
        parent_matrix_index=settings.parent_matrix_index,
    )

    if "matrices" in extra:
        try:
            mat.matrices = tuple(tuple(float(v) for v in m)
                                 for m in extra["matrices"])
        except (ValueError, TypeError):
            pass
    if "padding2" in extra:
        mat.padding2 = tuple(extra["padding2"])[:2]
    if "padding124" in extra:
        try:
            mat.padding124 = bytes.fromhex(extra["padding124"])
        except ValueError:
            pass

    mat.textures = [(slot.type_as_int(), slot.path)
                    for slot in settings.textures if slot.path]
    mat.string_params = [(int(i), str(v))
                         for i, v in extra.get("string_params", [])]
    mat.float_params = [(int(i), float(v))
                        for i, v in extra.get("float_params", [])]
    int_params = []
    if settings.alpha_mode != "NONE":
        alpha = (rf.ALPHA_MODE_TRANSPARENT
                 if settings.alpha_mode == "TRANSPARENT"
                 else rf.ALPHA_MODE_OPAQUE)
        int_params.append((rf.INT_PARAM_ALPHA, alpha))
    int_params += [(int(i), int(v)) for i, v in extra.get("int_params", [])
                   if int(i) != rf.INT_PARAM_ALPHA]
    mat.int_params = int_params
    mat.vec4_params = [(int(i), tuple(float(x) for x in v))
                       for i, v in extra.get("vec4_params", [])]

    if options.get("write_attach_points", True):
        mat.attachment_points = list(attach_points)
    return mat


def build_model(context, obj, options, attach_points, attach_names,
                warnings, decimate_ratio=1.0) -> rf.RmvModel | None:
    settings = obj.rmv2
    scale = options.get("global_scale", 1.0)

    arrays = extract_mesh_arrays(context, obj, options, warnings,
                                 decimate_ratio)
    if arrays is None:
        warnings.append(f"{obj.name}: no exportable faces; skipped")
        return None
    try:
        me = arrays["mesh_for_weights"]
        bone_map = resolve_bone_map(obj, attach_names, warnings)
        fmt = _resolve_vertex_format(settings, bone_map, me, warnings,
                                     obj.name)
        k = {rf.VF_WEIGHTED: 2, rf.VF_CINEMATIC: 4}.get(fmt, 0)

        if k:
            vert_bone_idx, vert_bone_wgt = _gather_weights(
                me, bone_map, k, obj.name, warnings)
        else:
            vert_bone_idx = np.zeros((len(me.vertices), 0), np.uint8)
            vert_bone_wgt = np.zeros((len(me.vertices), 0), np.float32)

        loop_vidx = arrays["loop_vidx"]
        nl = len(loop_vidx)

        # --- convert to game space -------------------------------------
        pos_game = utils.blender_to_game(
            arrays["positions_pivot_rel"]) * scale
        normals_g = utils.blender_to_game(arrays["normals"])
        tangents_g = utils.blender_to_game(arrays["tangents"])
        binormals_g = utils.blender_to_game(arrays["binormals"])
        uv0 = utils.flip_uv_v(arrays["uv0"])
        uv1 = (utils.flip_uv_v(arrays["uv1"])
               if arrays["uv1"] is not None
               else np.zeros((nl, 2), np.float32))
        colours = arrays["colours"]

        # --- quantize to file precision, then weld ----------------------
        n_q = _encode_dirs(normals_g)
        t_q = _encode_dirs(tangents_g)
        b_q = _encode_dirs(binormals_g)
        uv0_q = uv0.astype(np.float16).view(np.uint16)
        uv1_q = uv1.astype(np.float16).view(np.uint16)
        col_q = np.clip(np.round(colours * 255.0), 0, 255).astype(np.uint8)

        key_dtype = np.dtype([
            ("vidx", "<i4"), ("n", "u1", (3,)), ("t", "u1", (3,)),
            ("b", "u1", (3,)), ("uv0", "<u2", (2,)), ("uv1", "<u2", (2,)),
            ("col", "u1", (4,)),
        ])
        keys = np.zeros(nl, dtype=key_dtype)
        keys["vidx"] = loop_vidx
        keys["n"] = n_q
        keys["t"] = t_q
        keys["b"] = b_q
        keys["uv0"] = uv0_q
        keys["uv1"] = uv1_q
        keys["col"] = col_q

        raw_view = keys.view(f"V{key_dtype.itemsize}").ravel()
        _, unique_idx, inverse = np.unique(
            raw_view, return_index=True, return_inverse=True)
        vcount = len(unique_idx)
        if vcount > 65536:
            raise ExportError(
                f"{obj.name}: {vcount} unique vertices after splitting "
                "(max 65536 for 16-bit indices). Reduce the mesh or split "
                "the object")

        src_vidx = loop_vidx[unique_idx]

        mesh = rf.RmvMeshData.empty(vcount, k)
        mesh.positions = pos_game[src_vidx]
        mesh.normals = _decode_dirs(n_q[unique_idx])
        mesh.tangents = _decode_dirs(t_q[unique_idx])
        mesh.binormals = _decode_dirs(b_q[unique_idx])
        mesh.uv0 = uv0_q[unique_idx].view(np.float16).astype(np.float32)
        mesh.uv1 = uv1_q[unique_idx].view(np.float16).astype(np.float32)
        mesh.colours = col_q[unique_idx].astype(np.float32) / 255.0
        if k:
            mesh.bone_indices = vert_bone_idx[src_vidx]
            mesh.bone_weights = vert_bone_wgt[src_vidx]

        tris = inverse[arrays["tri_loops"].reshape(-1)].reshape(-1, 3)
        if not arrays["mirrored"]:
            # The Blender->game map is a reflection (det -1), flipping
            # orientation once, so winding is reversed to compensate. An
            # object's own mirrored (negative-determinant) transform flips
            # a second time and the two cancel out.
            tris = utils.reverse_winding(tris)
        mesh.indices = tris.ravel().astype(np.uint16)

        material = _material_from_settings(obj, settings, fmt, scale,
                                           attach_points, options)

        shader_name = settings.shader_name or rf.DEFAULT_SHADER_NAME
        extra = {}
        if settings.extra_json:
            try:
                extra = json.loads(settings.extra_json)
            except ValueError:
                extra = {}
        shader_extra = b"\0" * 10
        shader_zero = b"\0" * 10
        try:
            if "shader_extra" in extra:
                shader_extra = bytes.fromhex(extra["shader_extra"])
            if "shader_zero" in extra:
                shader_zero = bytes.fromhex(extra["shader_zero"])
        except ValueError:
            pass

        return rf.RmvModel(
            material=material,
            mesh=mesh,
            render_flag=settings.render_flag,
            shader_name=shader_name,
            shader_extra=shader_extra,
            shader_zero=shader_zero,
        )
    finally:
        arrays["source_object"].to_mesh_clear()


def _encode_dirs(vec: np.ndarray) -> np.ndarray:
    """(n,3) [-1,1] -> (n,3) uint8 (same quantization as the file)."""
    return np.clip(np.round((vec + 1.0) * 0.5 * 255.0), 0, 255) \
        .astype(np.uint8)


def _decode_dirs(b: np.ndarray) -> np.ndarray:
    return b.astype(np.float32) / 255.0 * 2.0 - 1.0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def export_file(context, filepath: str, options: dict):
    """Export to filepath. Returns (stats, warnings). Raises ExportError."""
    warnings: list[str] = []

    root, lods = gather_lods(context, options)
    total_objects = sum(len(objs) for _, objs in lods)
    if total_objects == 0:
        raise ExportError(
            "Nothing to export: no mesh objects found in the active "
            "collection / selection")

    if options.get("auto_lods", False):
        if len(lods) == 1:
            lods = expand_auto_lods(lods,
                                    max(2, options.get("auto_lod_count", 4)))
        else:
            warnings.append(
                f"Generate LODs ignored: the source already has "
                f"{len(lods)} LODs")

    version = int(options.get("version", "7"))
    skeleton = options.get("skeleton_name", "")
    if not skeleton and root is not None:
        skeleton = root.rmv2.skeleton_name

    all_objects = [o for _, objs in lods for o in objs]
    attach_points = []
    if options.get("write_attach_points", True):
        attach_points = load_attach_points(root, all_objects)
    attach_names = {ap.name: ap.bone_index for ap in attach_points}

    rmv = rf.RmvFile(version=version, skeleton_name=skeleton)

    built_cache: dict = {}
    any_skinned = False
    for info, objs in lods:
        lod = rf.RmvLod(
            camera_distance=info["camera_distance"],
            lod_level=info["level"],
            quality_level=info["quality_level"],
        )
        ratio = info.get("decimate_ratio", 1.0)
        for obj in objs:
            cache_key = (obj.name, ratio)
            if cache_key in built_cache:
                model = built_cache[cache_key]
            else:
                model = build_model(context, obj, options, attach_points,
                                    attach_names, warnings, ratio)
                built_cache[cache_key] = model
            if model is None:
                continue
            if model.material.vertex_format in (rf.VF_WEIGHTED,
                                                rf.VF_CINEMATIC):
                any_skinned = True
            lod.models.append(model)
        if not lod.models:
            raise ExportError(
                f"LOD {info['level']}: no exportable meshes")
        rmv.lods.append(lod)

    if any_skinned and not skeleton:
        warnings.append(
            "File contains skinned meshes but no skeleton name is set "
            "(Collection > RMV2 settings or export option). The game needs "
            "it to animate the model")

    blob = rf.save(rmv, high_precision=options.get("high_precision", True))
    with open(filepath, "wb") as handle:
        handle.write(blob)

    stats = {
        "lods": len(rmv.lods),
        "meshes": sum(len(lod.models) for lod in rmv.lods),
        "vertices": sum(m.mesh.vertex_count
                        for lod in rmv.lods for m in lod.models),
        "triangles": sum(len(m.mesh.indices) // 3
                         for lod in rmv.lods for m in lod.models),
        "bytes": len(blob),
    }
    return stats, warnings
