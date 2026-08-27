"""Importer: Shogun 2 .variant_part_mesh -> Blender.

A VMPF file is one unit part - a helmet, torso, mask, saddle - carrying
its own LOD ladder and skinned to a shared skeleton.  The scene layout is
the same one the .rigid_model_v2 importer builds, so the RMV2 panels and
the .anim importer keep working unchanged:

    <name>              collection, rmv2.is_rmv2_root
      <name>_lod0..N    collections (any child of a root is a LOD)
        <object>        one mesh object per LOD

Skinned meshes arrive the ordinary way - vertex groups plus an armature
modifier - because that is what the format is: up to two weighted bone
influences per vertex, i.e. exactly CA's later "Weighted2".

Rigid parts are not skinned at all.  Each one rides a single bone named
in its own header (`VmpfPart.bone_index`) and stores its vertices in
that bone's space, so it gets a Child Of constraint like an
.animatable_rigid_model object rather than a vertex group - the same
treatment .variant_weighted_mesh attachments get.  Several of those
mounts (`Weapon1`, `Weapon2`, `Weapon3` on man_shogun) sit at the world
origin in the reference pose and are placed by the animation at
runtime, so a prop on one of them is *meant* to sit at the origin until
an animation moves it.

The one thing that differs from every other format this add-on reads is
that VMPF stores no model-space position at all.  Each vertex holds its
position once per influence, in that influence's own bone space, so the
mesh cannot be built without a bind pose.  Two sources, in order:

* the selected armature (from importing the matching reference .anim) -
  complete and authoritative;
* failing that, the bind pose recovered from the file's own geometry
  (vmpf_format.bind_frames_from_geometry), which places every bone that
  shares a two-influence vertex with the rest of the model.  Good enough
  to look at; bones it cannot reach are reported.

The file names the skeleton it wants ('man_shogun', 'horse', 'deer' ...),
which is stored on the root collection so the user is told which
reference .anim to load rather than having to guess.
"""

from __future__ import annotations

import json
import os

import bpy
import numpy as np

from . import mesh_build, scene_layout, skeleton, utils
from .properties import (fill_material_names, fill_shader_params,
                         set_format_version)
from . import vmpf_format as vf


class VmpfImportError(Exception):
    pass


# A geometry-derived bind pose above this much error (as a fraction of
# the model's size) is worth telling the user about; a real skeleton
# scores essentially zero. Six of the ~350 vanilla skinned meshes do.
_BIND_ERROR_LIMIT = 0.02


def _influences(channels: dict) -> tuple:
    """The per-vertex (bone index, weight) columns as (n, k) arrays.

    A vertex can name the same bone in both slots; mesh_build sums those
    rather than letting the second overwrite the first.
    """
    weight0 = channels["weight0"]
    if "bone1" not in channels:
        return channels["bone0"][:, None], weight0[:, None]
    return (np.stack([channels["bone0"], channels["bone1"]], 1),
            np.stack([weight0, 1.0 - weight0], 1))


def _build_mesh_object(model, part, name: str, scale: float, frames: dict,
                       bone_names: dict, warnings: list):
    channels = vf.decode_vertices(model, part)

    if model.is_skinned:
        positions_g, normals_g, placed = vf.skin(channels, frames)
        if not placed.all():
            missing = sorted(
                set(channels["bone0"][~placed].tolist())
                | set(channels.get("bone1", channels["bone0"])[~placed]
                      .tolist()))
            warnings.append(
                f"{name}: no bind pose for bone(s) "
                f"{', '.join(str(b) for b in missing[:6])}"
                f"{'...' if len(missing) > 6 else ''}; "
                f"{np.count_nonzero(~placed)} vertex/vertices left at that "
                "bone's origin. Import the reference .anim "
                f"('{model.skeleton_name}') first for a correct result")
    else:
        positions_g = channels["position0"]
        normals_g = channels["normal0"]

    positions_b = utils.game_to_blender(positions_g) * scale
    # The game->Blender map is a reflection, so winding flips once.
    indices = np.asarray(part.indices, np.int64)
    tris = utils.reverse_winding(
        indices[:(len(indices) // 3) * 3].reshape(-1, 3))
    try:
        me, loop_vidx = mesh_build.build_mesh(name, positions_b, tris,
                                              warnings)
    except mesh_build.MeshBuildError as exc:
        raise VmpfImportError(str(exc)) from exc

    # The rigid vertex carries a second UV set - the ambient occlusion
    # channel - zero in all but a handful of files, so an all-zero one is
    # not worth a layer.
    mesh_build.add_uv_layer(me, "UVMap", utils.flip_uv_v(channels["uv"]),
                            loop_vidx)
    second = channels.get("uv1")
    if second is not None and second.any():
        mesh_build.add_uv_layer(me, "UVMap_1", utils.flip_uv_v(second),
                                loop_vidx)

    if "colour" in channels:
        mesh_build.add_colour_attribute(me, channels["colour"])

    # Zero-length normals (a vertex whose influences cancelled, or one
    # left unplaced) are dropped by set_custom_normals' own guard.
    mesh_build.set_custom_normals(
        me, utils.normalize_rows(utils.game_to_blender(normals_g)))
    me.update()

    obj = bpy.data.objects.new(name, me)
    if model.is_skinned:
        mesh_build.add_vertex_groups(obj, *_influences(channels), bone_names)
    else:
        # A rigid part rides one bone whole, so it gets a constraint
        # (added by the caller, which has the armature) and no vertex
        # groups - having none is also what marks it as rigid on the way
        # back out.  decode_vertices reports bone 0 at full weight for
        # every rigid vertex; that is a placeholder keeping the channel
        # dict one shape, not an influence, and binding it would weld
        # every prop in the game to the skeleton's first bone.
        obj.rmv2.matrix_index = part.bone_index
    obj.rmv2.model_name = name[:31]
    obj.rmv2.textures_initialized = True
    # The file has no vertex-format field - the layout follows from the
    # header - so record which one this mesh came in with, exactly as the
    # Shogun 2 .rigid_model_v2 path does. The exporter reads it back, and
    # the RMV2 Object panel shows and can change it.
    obj.rmv2.vertex_format = ("VMPF_SKINNED" if model.is_skinned
                              else "VMPF_RIGID")
    return obj


def _attach_rigid_part(obj, armature, bone_names: dict, part, name: str,
                       warnings: list) -> None:
    """Hang a rigid part off the bone its header names.

    The vertices are already in that bone's space, so the constraint's
    inverse is forced to identity (skeleton.attach_to_bone) and the
    bone's rest transform is what carries the part into place - exactly
    how the .variant_weighted_mesh attachments and the destructible
    -building pieces are bound.
    """
    bone_name = bone_names.get(part.bone_index)
    if bone_name is None:
        warnings.append(
            f"{name}: bone index {part.bone_index} is not on armature "
            f"'{armature.name}' ({len(bone_names)} bones); left "
            "unattached at that bone's origin")
        return
    skeleton.attach_matrix_index_mesh(obj, armature, bone_name)


def _store_part_metadata(obj, part, index: int) -> None:
    """Keep the library fields a format-2 export has to reproduce.

    The name as written, the attachment slot, the per-part material names
    and the original ordering - vanilla libraries do not always keep a
    prop's LODs contiguous, and re-ordering them is a needless diff.
    """
    obj.rmv2.extra_json = json.dumps({
        "vmpf_part_name": part.name,
        "vmpf_bone_index": part.bone_index,
        "vmpf_material_names": list(part.material_names),
        "vmpf_part_index": index,
    })
    # Also as an editable list, because three names is the whole of what
    # this format says about the part's surface and it had nowhere to
    # show. extra_json stays the round-trip record for a .blend saved
    # before this existed; export prefers the list when it has one.
    fill_material_names(obj.rmv2, part.material_names)


def _lod_layout(model, stem: str) -> list:
    """[(lod_level, object_name, part, part_index)] for the file.

    Vertex formats 0 and 1 store a straight LOD ladder, one part per
    level.  Format 2 is a *library*: many unrelated props in one file
    (`equipment/mesh1` holds 52 of them in 142 parts), each part named
    '<prop>_lod<N>', so the LOD comes from the name and props are told
    apart by the stem.
    """
    if model.vertex_format != vf.VF_RIGID_NAMED:
        multiple = len(model.parts) > 1
        return [(i, f"{stem}_lod{i}" if multiple else stem, part, i)
                for i, part in enumerate(model.parts)]

    rows = []
    for index, part in enumerate(model.parts):
        match = scene_layout.LOD_SUFFIX.match(part.name or "")
        if match:
            rows.append([match.group(1), int(match.group(2)), part, index])
        else:
            # A part with no _lodN suffix is a prop all of its own, with
            # a single level. equipment/mesh1 has two of these.
            rows.append([part.name or f"{stem}_part{index:03d}", None,
                         part, index])

    # CA's ladders normally start at lod1, but not always - in mesh1
    # rigid_equip_Yumi starts at lod2 - so each prop is normalised
    # against its OWN finest level. Using one floor for the whole file
    # would push every prop down a level whenever any one of them
    # started lower, and then LOD 0 would hold almost nothing.
    floors: dict = {}
    for prop, lod, _, _ in rows:
        if lod is not None:
            floors[prop] = min(floors.get(prop, lod), lod)

    out = []
    for prop, lod, part, index in rows:
        level = 0 if lod is None else lod - floors[prop]
        out.append((level, f"{prop}_lod{level}", part, index))
    return out


def import_file(context, filepath: str, options: dict):
    """Import one .variant_part_mesh. Returns (root, stats, warnings)."""
    with open(filepath, "rb") as handle:
        data = handle.read()
    model = vf.load(data)

    warnings: list = []
    stem = os.path.splitext(os.path.basename(filepath))[0]
    # Every unit part in the game is called "mesh.variant_part_mesh"; the
    # folder is what actually names it.
    if stem.lower() == "mesh":
        parent = os.path.basename(os.path.dirname(filepath))
        if parent:
            stem = parent
    scale = options.get("global_scale", 1.0)

    root = scene_layout.new_root(context, stem, model.skeleton_name)
    set_format_version(root.rmv2, "VMPF", model.version)
    # File-wide, like .variant_weighted_mesh's - see RMV2ShaderParam.
    fill_shader_params(root.rmv2, model.float_params, model.vec4_params)
    # A non-library file names its three materials once, in the
    # trailer after the skeleton name; a library names them per
    # part instead and leaves this empty.
    fill_material_names(root.rmv2, model.material_names)

    armature = None
    if options.get("attach_armature", True):
        armature = skeleton.find_context_armature(context)
    if armature is not None:
        scene_layout.adopt_armature(root, armature)

    bone_names = skeleton.bone_name_by_index(armature) if armature else {}
    frames: dict = {}
    source = "none"
    if model.is_skinned:
        if armature is not None:
            frames = skeleton.bind_frames_in_game_space(armature, scale)
            source = "armature"
        else:
            first = model.parts[0] if model.parts else None
            if first is not None:
                frames = vf.bind_frames_from_geometry(
                    vf.decode_vertices(model, first))
            source = "geometry"
            if frames:
                error = vf.bind_frame_error(
                    vf.decode_vertices(model, first), frames)
                detail = (f" and looks unreliable (about "
                          f"{error * 100:.0f}% of the model's size out of "
                          f"place)" if error > _BIND_ERROR_LIMIT else "")
                warnings.append(
                    f"No armature selected, so the bind pose was rebuilt "
                    f"from the mesh itself ({len(frames)} bones){detail}. "
                    f"Import '{model.skeleton_name}' (the reference .anim) "
                    "and re-import for a correct, complete result")
            else:
                warnings.append(
                    f"{stem}: this mesh has no two-influence vertices, so "
                    "its bind pose cannot be rebuilt from geometry; import "
                    f"the reference .anim for '{model.skeleton_name}' first")

    layout = _lod_layout(model, stem)
    if options.get("import_lods", "ALL") == "FIRST":
        # Each prop was normalised against its own finest level, so this
        # keeps every prop's best LOD, not just the first one in the file.
        layout = [row for row in layout if row[0] == 0]
    lod_collections: dict = {}
    stats = {"lods": 0, "meshes": 0, "vertices": 0, "triangles": 0,
             "hidden": 0, "bind_source": source}
    built = []

    for level, name, part, part_index in layout:
        col = lod_collections.get(level)
        if col is None:
            col = scene_layout.new_lod(root, f"{stem}_lod{level}", level)
            lod_collections[level] = col
            stats["lods"] += 1

        obj = _build_mesh_object(model, part, name, scale, frames,
                                 bone_names, warnings)
        if model.vertex_format == vf.VF_RIGID_NAMED:
            _store_part_metadata(obj, part, part_index)
        col.objects.link(obj)
        if armature is not None:
            if obj.vertex_groups:
                skeleton.attach_mesh(obj, armature)
            elif part.bone_index >= 0:
                _attach_rigid_part(obj, armature, bone_names, part, name,
                                   warnings)
        built.append(obj)
        stats["meshes"] += 1
        stats["vertices"] += part.vertex_count
        stats["triangles"] += len(part.indices) // 3

    if model.vertex_format == vf.VF_RIGID_NAMED:
        # A library is many unrelated props in one file - equipment/mesh1
        # holds 52 of them - so every prop shares the origin and showing
        # them all is a pile, not a model. Hiding all but the first is
        # the useful default, and hiding LOD collections on top of it
        # would only take away the levels of the prop being looked at.
        stats["hidden"] = scene_layout.show_only_first(built)
    else:
        scene_layout.show_only_most_detailed_lod(
            context, lod_collections.items())

    # This format's parts are its LOD ladder, so Export >
    # Generate LODs applies and its override rows should start
    # populated rather than empty - same as after an RMV2
    # import. See export_rmv2.default_lod_overrides, whose
    # numbers here are an untested guess (TODO 3b).
    from .export_rmv2 import default_lod_overrides
    default_lod_overrides(root, rigged=model.is_skinned)

    return root, stats, warnings
