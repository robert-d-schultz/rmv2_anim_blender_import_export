"""The collection layout every importer in this add-on builds.

    <name>              collection, rmv2.is_rmv2_root
      <name>_lod0..N    collections, rmv2.is_lod
        <object>        one or more mesh objects

.rigid_model_v2 defined it; the Shogun 2 importers
(.animatable_rigid_model / .rigid_model and .variant_part_mesh) follow it
so the RMV2 panels, the .anim importer and the exporters' LOD gathering
keep working whichever format a model came from.  Keeping the code that
builds it in one place is what stops the three importers drifting apart.
"""

from __future__ import annotations

import re

import bpy

# "<name>_lod<N>", the suffix the layout puts on LOD collections and on
# the objects inside them.  .variant_part_mesh libraries use the same
# spelling inside the file itself, which is how one file full of
# unrelated props is told apart into props and levels.  Blender's own
# ".001" duplicate suffix is stripped first (utils.strip_blender_suffix).
LOD_SUFFIX = re.compile(r"^(.*)_lod(\d+)$", re.IGNORECASE)


def new_root(context, name: str, skeleton_name: str = ""):
    """A fresh root collection, linked into the scene."""
    root = bpy.data.collections.new(name)
    context.scene.collection.children.link(root)
    root.rmv2.is_rmv2_root = True
    root.rmv2.skeleton_name = skeleton_name
    return root


def find_root(name: str):
    """An existing root collection called `name`, or None.

    Formats that put each LOD in its OWN file (.variant_weighted_mesh,
    where a unit ships as `<unit>_lod1` ... `<unit>_lod4`) use this so
    that importing the levels one after another fills in a single ladder
    instead of making four unrelated roots.
    """
    collection = bpy.data.collections.get(name)
    if collection is not None and collection.rmv2.is_rmv2_root:
        return collection
    return None


def find_or_new_lod(root, name: str, level: int):
    """The LOD collection for `level` under `root`, created if absent."""
    for child in root.children:
        if child.rmv2.is_lod and child.rmv2.lod_level == level:
            return child
    return new_lod(root, name, level)


def new_lod(root, name: str, level: int):
    """A fresh LOD collection under `root`."""
    col = bpy.data.collections.new(name)
    root.children.link(col)
    col.rmv2.is_lod = True
    col.rmv2.lod_level = level
    return col


def find_layer_collection(layer_collection, collection):
    """The view-layer entry for `collection`, or None."""
    if layer_collection.collection is collection:
        return layer_collection
    for child in layer_collection.children:
        found = find_layer_collection(child, collection)
        if found is not None:
            return found
    return None


def show_only_most_detailed_lod(context, levels) -> None:
    """Hide every LOD collection but the most detailed one in the current
    view layer, so the viewport is not cluttered with overlapping LODs
    right after import.  Reversible via the outliner's eye icon.

    `levels` is an iterable of (lod level, collection) pairs.
    """
    pairs = list(levels)
    if len(pairs) < 2:
        return
    finest = min(level for level, _ in pairs)
    root_layer = context.view_layer.layer_collection
    for level, col in pairs:
        layer = find_layer_collection(root_layer, col)
        if layer is not None:
            layer.hide_viewport = level != finest


def adopt_armature(root, armature) -> None:
    """Move an existing armature into the model's root collection so the
    skeleton travels with the model it now drives.  Links into other RMV2
    roots are kept - one skeleton may serve several imported models."""
    for col in list(armature.users_collection):
        if not col.rmv2.is_rmv2_root:
            col.objects.unlink(armature)
    if armature.name not in root.objects:
        root.objects.link(armature)
