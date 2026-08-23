"""Importer: Empire/Napoleon .variant_weighted_mesh -> Blender.

A VWM file is one unit at one LOD, holding every body part its variants
can draw - `<unit>_head01`, `<unit>_body02`, `<unit>_legs01` and so on -
each a separate object here.  The LOD ladder lives in *separate files*
(`<unit>_lod1` ... `<unit>_lod4`), unlike .rigid_model_v2 where one file
holds the whole ladder, so importing the four files in any order fills
in one root collection rather than making four unrelated ones:

    <unit>              collection, rmv2.is_rmv2_root
      <unit>_lod0..3    collections, rmv2.is_lod
        <part>          one mesh object per part

CA numbers the levels from 1; they are shifted to Blender-side 0 so the
finest level is LOD 0, matching every other importer in this add-on.

Meshes are skinned the ordinary way - vertex groups plus an armature
modifier - since that is exactly what the format stores: one to eight
weighted bone influences per vertex.

A file can also carry *attachments*: the props the unit hangs off a
single bone - muskets, backpacks, flagpoles.  Those are rigid
.animatable_rigid_model objects, so they are imported the way the ARM
importer does it, with a Child Of constraint and no vertex groups.
Empire's unitmodels/euro_equipment.variant_weighted_mesh is nothing but
134 of them.

The catch, shared with Shogun 2's .variant_part_mesh, is that VWM holds
no model-space position at all: every vertex is stored once per
influence, in that influence's own bone space.  So the mesh simply
cannot be built without a bind pose, and unlike VMPF this format does
not even name the skeleton it wants.  It does not have to: every unit
model in Empire is rigged to the same 41-bone skeleton, shipped as
`animations/reference/tpose.anim`, so the importer asks for that by name
rather than guessing.
"""

from __future__ import annotations

import os
import re

import bpy
import numpy as np

from . import arm_format as armf
from . import import_arm, materials, mesh_build, scene_layout, skeleton
from . import utils
from . import vwm_format as vf

# The reference skeleton every Empire unit model is rigged to.
REFERENCE_SKELETON = "animations/reference/tpose.anim"

# Textures are not named in the file; they follow the unit's own name in
# a fixed folder, e.g. african_slaver_musketeers ->
# unitmodels/textures/african_slaver_musketeers_diffuse.dds.
_TEXTURE_DIR = "unitmodels/textures"
_TEXTURE_SUFFIXES = (
    ("_diffuse", 0),        # diffuse
    ("_normal", 1),         # normal
    ("_gloss_map", 12),     # gloss
)

_LOD_SUFFIX = re.compile(r"^(.*)_lod(\d+)$", re.IGNORECASE)


class VwmImportError(Exception):
    pass


def split_lod(stem: str) -> tuple:
    """('african_slaver_musketeers_lod4') -> ('african_...', 3).

    CA's ladders start at lod1, so the number is shifted down to put the
    finest level at 0.  A file with no suffix is a lone level 0.
    """
    match = _LOD_SUFFIX.match(stem)
    if not match:
        return stem, 0
    return match.group(1), max(0, int(match.group(2)) - 1)


def _build_mesh_object(part, name: str, scale: float, frames: dict,
                       bone_names: dict, warnings: list):
    positions_g, normals_g, placed = vf.skin(part, frames)
    if not placed.all():
        missing = sorted(set(
            int(b) for b in np.asarray(part.influence_bones)
            if int(b) not in frames))
        warnings.append(
            f"{name}: the armature has no bone for index(es) "
            f"{', '.join(str(b) for b in missing[:6])}"
            f"{'...' if len(missing) > 6 else ''}; "
            f"{int((~placed).sum())} vertex/vertices are left at the "
            "origin. Is this the right skeleton?")

    positions_b = utils.game_to_blender(positions_g) * scale
    # The game->Blender map is a reflection, so winding flips once.
    indices = np.asarray(part.indices, np.int64)
    tris = utils.reverse_winding(
        indices[:(len(indices) // 3) * 3].reshape(-1, 3))
    try:
        me, loop_vidx = mesh_build.build_mesh(name, positions_b, tris,
                                              warnings)
    except mesh_build.MeshBuildError as exc:
        raise VwmImportError(str(exc)) from exc

    mesh_build.add_uv_layer(me, "UVMap", utils.flip_uv_v(part.uv), loop_vidx)
    mesh_build.set_custom_normals(
        me, utils.normalize_rows(utils.game_to_blender(normals_g)))
    me.update()

    obj = bpy.data.objects.new(name, me)
    bones, weights = vf.influence_table(part)
    mesh_build.add_vertex_groups(obj, bones, weights, bone_names)
    obj.rmv2.model_name = part.name[:31]
    obj.rmv2.textures_initialized = True
    return obj


def _build_attachment(entry, index: int, scale: float, armature,
                      bone_names: dict, options: dict, warnings: list):
    """One prop -> one Blender object, bound to the bone it rides.

    The geometry is an ordinary ARM object, so import_arm builds it and
    this only has to say which bone it belongs to - the weighted mesh
    keeps that outside the object rather than after it.
    """
    name = entry.name or f"attachment_{index:02d}"
    obj = import_arm._build_mesh_object(entry.mesh, name, scale, warnings)
    import_arm._fill_settings(obj, entry.mesh, index)
    obj.rmv2.model_name = name[:31]
    # Having no vertex groups is what marks an object as an attachment
    # rather than a skinned part, on the way back out too.  The bone is
    # stored here in case there is no armature to bind to; once the Child
    # Of constraint below exists, that is the source of truth and
    # attach_matrix_index_mesh clears this again.
    obj.rmv2.matrix_index = entry.bone

    if armature is not None:
        bone_name = bone_names.get(entry.bone)
        if bone_name is None:
            warnings.append(
                f"{name}: bone index {entry.bone} is not on armature "
                f"'{armature.name}' ({len(bone_names)} bones); left "
                "unattached")
        else:
            skeleton.attach_matrix_index_mesh(obj, armature, bone_name)

    if options.get("build_materials", True):
        pairs = [(import_arm._TEXTURE_TYPE_BY_SLOT[slot], path)
                 for slot, path in zip(armf.TEXTURE_SLOTS,
                                       entry.mesh.textures) if path]
        if pairs:
            material = materials.build_material(
                name, pairs, "NONE", options.get("texture_root", ""))
            if material is not None:
                obj.data.materials.append(material)
    return obj


def _unit_textures(base: str, texture_root: str) -> list:
    """[(texture type, pack-relative path)] that actually exist on disk.

    The file names no textures, so these are the by-convention paths for
    the unit.  Ones the user has not extracted are dropped rather than
    left as dead image nodes.
    """
    pairs = []
    for suffix, ttype in _TEXTURE_SUFFIXES:
        path = f"{_TEXTURE_DIR}/{base}{suffix}.dds"
        if materials.resolve_texture_path(path, texture_root):
            pairs.append((ttype, path))
    return pairs


def import_file(context, filepath: str, options: dict):
    """Import one .variant_weighted_mesh. Returns (root, stats, warnings)."""
    with open(filepath, "rb") as handle:
        data = handle.read()
    model = vf.load(data)

    warnings: list = []
    stem = os.path.splitext(os.path.basename(filepath))[0]
    base, level = split_lod(stem)
    scale = options.get("global_scale", 1.0)

    armature = None
    if options.get("attach_armature", True):
        armature = skeleton.find_context_armature(context)
    if armature is None and model.parts:
        raise VwmImportError(
            "This format stores no model-space positions - every vertex "
            "lives in its bones' spaces - so it cannot be imported "
            "without a skeleton. Import the reference skeleton "
            f"'{REFERENCE_SKELETON}' with File > Import > Total War "
            "Animation (.anim) first, leave its armature selected, then "
            "import this file again")

    # A file of nothing but attachments (euro_equipment) is rigid
    # throughout, so it can be read without one.
    frames = ({} if armature is None
              else skeleton.bind_frames_in_game_space(armature, scale))
    bone_names = ({} if armature is None
                  else skeleton.bone_name_by_index(armature))

    root = scene_layout.find_root(base)
    if root is None:
        root = scene_layout.new_root(context, base, REFERENCE_SKELETON)
    root.rmv2.vwm_version = str(model.version)
    scene_layout.adopt_armature(root, armature)
    lod = scene_layout.find_or_new_lod(root, f"{base}_lod{level}", level)

    texture_pairs = []
    if options.get("build_materials", True):
        texture_pairs = _unit_textures(
            base, options.get("texture_root", ""))
        if not texture_pairs:
            warnings.append(
                f"{base}: no textures found under '{_TEXTURE_DIR}/'; set "
                "the add-on's Texture Root Directory to a folder of "
                "extracted game textures to get materials")

    stats = {"meshes": 0, "vertices": 0, "triangles": 0, "lods": 1,
             "attachments": 0}
    for part in model.parts:
        name = part.name or f"{base}_part{stats['meshes']:02d}"
        obj = _build_mesh_object(part, name, scale, frames, bone_names,
                                 warnings)
        lod.objects.link(obj)
        if texture_pairs:
            material = materials.build_material(
                name, texture_pairs, "NONE", options.get("texture_root", ""))
            if material is not None:
                obj.data.materials.append(material)
        if obj.vertex_groups:
            skeleton.attach_mesh(obj, armature)
        stats["meshes"] += 1
        stats["vertices"] += part.vertex_count
        stats["triangles"] += len(part.indices) // 3

    for index, entry in enumerate(model.attachments):
        obj = _build_attachment(entry, index, scale, armature, bone_names,
                                options, warnings)
        lod.objects.link(obj)
        stats["meshes"] += 1
        stats["attachments"] += 1
        stats["vertices"] += entry.mesh.vertex_count
        stats["triangles"] += len(entry.mesh.indices) // 3

    levels = [(child.rmv2.lod_level, child)
              for child in root.children if child.rmv2.is_lod]
    scene_layout.show_only_most_detailed_lod(context, levels)
    return root, stats, warnings
