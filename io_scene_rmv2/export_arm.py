"""Exporter: Blender objects -> Shogun 2 .animatable_rigid_model /
.rigid_model.

The inverse of import_arm.  Each exported object becomes one file object
welded to a single bone, so the bone is taken from the object's Child Of
constraint (preferred, since re-parenting in Blender should be what
counts) or its stored matrix_index, exactly like the RMV2 exporter does.

Which of the two formats is written follows the chosen file extension:
`.rigid_model` has no per-object bone index and closes with a bounding
box, the animatable form is the other way round.

The format stores full float32 positions, normals, tangents and UVs, so
unlike .rigid_model_v2 nothing is quantized on the way out.  Vertices are
split per loop (the format has one UV and one normal per vertex, with no
way to express a seam otherwise) and then welded back where identical.
"""

from __future__ import annotations

import numpy as np

from . import arm_format as armf
from . import export_rmv2, skeleton
from .properties import (chosen_version, root_format_version,
                         shader_params_or_default)


class ArmExportError(Exception):
    pass


# Precision of the vertex-welding key (see export_rmv2.weld_loops).
# Positions and UVs are kept far finer than .rigid_model_v2's, because
# ARM stores full float32 and can actually hold the extra precision.
_POSITION_GRID = 1e5
_UV_GRID = 1e5


def _gather_objects(context, options: dict) -> tuple:
    """(root collection or None, the mesh objects to write).

    The root comes back too because it is where the object version is
    recorded - see export_file.
    """
    source = options.get("source", "AUTO")
    root, lods = export_rmv2.gather_lods(context, {"source": "AUTO"})
    if source == "SELECTED":
        objects = [o for o in context.selected_objects if o.type == "MESH"]
    elif source == "VISIBLE":
        objects = [o for o in context.view_layer.objects
                   if o.type == "MESH" and o.visible_get()]
    else:
        objects = []
        if lods:
            # An ARM file has no LOD tree of its own - one file is one LOD,
            # so take the most detailed one.
            best = min(lods, key=lambda item: item[0]["level"])
            objects = list(best[1])
        if not objects:
            objects = [o for o in context.selected_objects
                       if o.type == "MESH"]
    return root, sorted(objects, key=lambda o: o.name)


def _bone_index(obj, armature, animatable: bool):
    """The bone this object rides, or None for a plain .rigid_model."""
    if not animatable:
        return None
    for con in obj.constraints:
        if (con.type == "CHILD_OF" and con.target is not None
                and con.target.type == "ARMATURE" and con.subtarget):
            index = skeleton.bone_index_by_name(con.target).get(
                con.subtarget)
            if index is not None:
                return index
    stored = obj.rmv2.matrix_index
    return max(0, stored)


def _build_mesh(context, obj, options, warnings) -> armf.ArmMesh | None:
    """Convert one Blender object into an ARM object."""
    scale = options.get("global_scale", 1.0)
    # ARM objects are welded to a bone and stored in its space, with no
    # pivot field - same as Shogun 2's .rigid_model_v2 (see
    # export_rmv2.extract_mesh_arrays).
    arrays = export_rmv2.extract_mesh_arrays(
        context, obj, dict(options, bone_local_space=True), warnings, 1.0)
    if arrays is None:
        warnings.append(f"{obj.name}: no exportable faces; skipped")
        return None
    try:
        # extract_mesh_arrays substitutes opaque black when the mesh has
        # no colour attribute; vanilla ARM files store white, and the
        # shader multiplies by it, so black would darken the model.
        colours = (arrays["colours"] if obj.data.color_attributes
                   else np.ones((len(arrays["loop_vidx"]), 4), np.float32))
        welded = export_rmv2.weld_loops(
            arrays, scale, _POSITION_GRID, _UV_GRID, colours=colours)

        mesh = armf.ArmMesh(version=options.get("arm_version", 5))
        mesh.positions = welded["positions"]
        mesh.normals = welded["normals"]
        mesh.uv0 = welded["uv0"]
        mesh.tangents = welded["tangents"]
        mesh.binormals = welded["binormals"]
        mesh.colours = welded["colours"]
        mesh.uv1 = welded["uv1"]
        mesh.indices = welded["indices"].astype(np.uint32)

        # Version 3 and below have no parameter block to put these in.
        if mesh.version >= armf._PARAMS_FROM_VERSION:
            mesh.float_params, mesh.vec4_params = shader_params_or_default(
                obj.rmv2, armf.default_params())
        elif obj.rmv2.shader_params:
            warnings.append(
                f"{obj.name}: object version {mesh.version} has no "
                f"parameter block, so its {len(obj.rmv2.shader_params)} "
                "shader parameter(s) were not written")
        slot_count, _ = armf.texture_layout(mesh.version)
        keep = armf.TEXTURE_SLOTS[:slot_count]
        dropped = []
        for slot, path in _textures_for(obj):
            if slot in keep:
                mesh.set_texture(slot, path)
            else:
                dropped.append(slot)
        if dropped:
            # An older object version simply has nowhere to put these.
            # Saying so is better than refusing the export outright: the
            # version was asked for deliberately.
            warnings.append(
                f"{obj.name}: object version {mesh.version} has only "
                f"{slot_count} texture slot(s), so its "
                f"{', '.join(dropped)} texture(s) were not written")
        return mesh
    finally:
        arrays["source_object"].to_mesh_clear()


_SLOT_BY_TEXTURE_TYPE = {0: "diffuse", 1: "normal", 12: "gloss", 5: "ao"}


def _textures_for(obj):
    """(slot, texture name) pairs from the object's RMV2 texture list.

    Shogun 2 names a bare texture *set* ("12_pounder_diffuse"), not a
    path, so anything path-like is reduced to its final component.
    """
    out = []
    for slot in obj.rmv2.textures:
        name = _SLOT_BY_TEXTURE_TYPE.get(slot.type_as_int())
        if name is None or not slot.path:
            continue
        bare = slot.path.replace("\\", "/").rsplit("/", 1)[-1]
        out.append((name, bare.rsplit(".", 1)[0]))
    return out


def export_file(context, filepath: str, options: dict):
    """Export to filepath. Returns (stats, warnings)."""
    warnings: list[str] = []
    root, objects = _gather_objects(context, options)
    if not objects:
        raise ArmExportError(
            "Nothing to export: select the mesh objects, or make the "
            "model's collection active")
    # The version is the model's own, off the root collection; the export
    # dialog has no field for it.
    options = dict(options, arm_version=int(chosen_version(
        options, "arm_version", root_format_version(root, "ARM", "5"))))

    # A plain .rigid_model has no per-object bone index and closes with a
    # bounding box; the animatable form is the other way round. The file
    # extension the user chose is what says which one to write.
    animatable = not filepath.lower().endswith(".rigid_model")
    armature = skeleton.find_context_armature(context)
    arm = armf.ArmFile()
    for obj in objects:
        mesh = _build_mesh(context, obj, options, warnings)
        if mesh is None:
            continue
        mesh.bone_index = _bone_index(obj, armature, animatable)
        arm.meshes.append(mesh)

    if not arm.meshes:
        raise ArmExportError("No exportable meshes")
    if not animatable:
        arm.bounding_box = arm.computed_bounding_box()

    blob = armf.save(arm)
    with open(filepath, "wb") as handle:
        handle.write(blob)

    stats = {
        "meshes": len(arm.meshes),
        "vertices": sum(m.vertex_count for m in arm.meshes),
        "triangles": sum(len(m.indices) // 3 for m in arm.meshes),
        "bytes": len(blob),
    }
    return stats, warnings
