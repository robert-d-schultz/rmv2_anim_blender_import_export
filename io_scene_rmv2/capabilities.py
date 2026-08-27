"""What each container can actually hold, in one table.

Every mesh format this add-on reads goes through the same Blender-side
shape - a root collection of LOD collections of mesh objects, with the
RMV2 panels on them - because that is what makes one importer's output
work with another's exporter, with the .anim importer, and with the LOD
machinery.  RMV2 defined that shape and the others are fitted into it.

Fitting them in is not the same as pretending they are the same file.
`.animatable_rigid_model` has no LOD tree, `.variant_part_mesh` has no
camera distance, `.variant_weighted_mesh` has no material id, and RMV2
itself gained fields as it went: a v1 mesh has no shader name, a v6 LOD
has no quality level.  Offering those anyway is offering the user a
setting that gets silently dropped on export, which is worse than not
offering it.

So the panels are drawn from this table rather than from a fixed list of
fields.  Everything here is a statement about a file format, checked
against the reader and writer that implement it:

* `.rigid_model_v2` - `rmv2_format`.  `_LOD_HEADER_V5_V6` is 20 bytes and
  stops after the camera distance; `_LOD_HEADER_V7_V8` is 28 and adds the
  LOD level and quality level.  `Shogun2Material` (v1-v3) writes a shader
  name only from v2, has no int params (so no alpha), no filters, no
  parent matrix index, no attachment points, and a texture slot count
  fixed by its material id.
* `.animatable_rigid_model` / `.rigid_model` - `arm_format`.  A flat
  object list: four texture slots, a bone index and a named parameter
  block per object, no LOD tree, no material id, no vertex-format field.
  The parameter block arrives with object version 4.
* `.variant_part_mesh` - `vmpf_format`.  Parts are the LOD ladder, with
  no camera distance or quality level anywhere; materials are three
  fixed names, so there are no texture slots to edit.  Its parameter
  block is written once for the file.
* `.variant_weighted_mesh` - `vwm_format`.  One file is one LOD, and the
  part name doubles as the texture set, so there are no texture slots
  either.  Its parameter block is also file-wide - which, with no
  texture slots and no material id, makes it most of the material.
"""

from __future__ import annotations

from . import arm_format as armf
from . import properties as props

# The Blender-side field each container writes.  Absent = the format has
# nowhere to put it, so the panel does not offer it.
#
# "matrix_index" means the object rides one bone whole. Every one of
# these containers has that; only what it is called differs.
_OBJECT_CAPS = {
    "RMV2": {
        "vertex_format", "material_id", "alpha_mode", "shader_name",
        "render_flag", "textures", "texture_directory", "matrix_index",
        "parent_matrix_index", "filters",
    },
    # No material id, no vertex-format field (the object version fixes the
    # layout), and the four texture slots are positional. The named
    # parameter block is per object here, and only from version 4.
    "ARM": {"textures", "matrix_index", "shader_params"},
    # The layout follows from the file header, so the vertex format is
    # kept per mesh. There are no texture slots - a part's material is
    # three names and nothing else - so those are what the panel shows
    # instead, and a library file keeps them per part.
    "VMPF": {"vertex_format", "matrix_index", "material_names"},
    # Skinned parts carry nothing of their own; an attachment is an ARM
    # object, and gets that row of the table instead (see object_caps).
    "VWM": {"matrix_index"},
}

# auto_lods - the Generate LODs (Decimate) export option and the
# override rows that drive it - follows `lod_ladder`, and for one
# reason: the decimator is Blender's own modifier and would happily run
# on any mesh here, so what decides it is whether the *container* has a
# ladder for the generated levels to go into.
#
# `.rigid_model_v2` has one (its LOD table) and so does
# `.variant_part_mesh` (its parts are the ladder). `.rigid_model` has no
# ladder at all. `.variant_weighted_mesh` and the ARM pair do have
# ladders, but across separate `_lod1`/`_lod2` *files* - generating one
# means writing four files, which is a different feature from expanding
# one export, and is not what these rows describe.
#
# "shader_params" is the named parameter block ("light_scale",
# "specfactor"). Where it sits is the whole reason it appears in both
# tables: `.variant_part_mesh` and `.variant_weighted_mesh` write one
# block for the file, ahead of the parts, so it belongs to the root
# collection - while `.animatable_rigid_model` writes one per object and
# vanilla files do vary between them, so that one is in _OBJECT_CAPS.
# `.rigid_model_v2` addresses its parameters by index rather than by
# name; those stay in extra_json.
#
# "lod_levels" and "lod_ladder" are different questions and only
# `.rigid_model_v2` and `.variant_part_mesh` answer yes to both.
#
# * "lod_ladder" - the *file* holds several levels, so a decimator has
#   somewhere to put what it generates.
# * "lod_levels" - a *model* can have more than one LOD collection at
#   all. `.variant_weighted_mesh` ships its levels as separate
#   `_lod1`..`_lod4` files that the importer merges into one root, so its
#   LOD Level means something real even though the file has no ladder.
#   The ARM pair is the one that genuinely cannot: one file is one LOD
#   and the importer makes exactly one collection, so offering a level
#   there is offering a number with nothing to vary.
_COLLECTION_CAPS = {
    "RMV2": {"lod_ladder", "lod_levels", "camera_distance", "auto_lods",
             "attach_points", "skeleton_name"},
    "ARM": {"skeleton_name"},
    "VMPF": {"lod_ladder", "lod_levels", "auto_lods", "skeleton_name",
             "shader_params", "material_names"},
    "VWM": {"lod_levels", "skeleton_name", "shader_params"},
}


# Which vertex formats a container can actually store.
#
# This is a *container* split and deliberately not a version one. Within
# `.rigid_model_v2` the version does not constrain the layout: Warhammer 3
# uses Weighted for a distant LOD of a mesh that is Cinematic up close,
# `army_banner_flag_general` is a v7 file storing the Shogun 2 layout, and
# `zen_garden_floor_stone_01` is a v1 file storing the modern Static one.
# A version filter here would be inventing a rule the files do not follow.
#
# What is real is that `.variant_part_mesh` has three layouts of its own,
# chosen by its header, and they are a different enum entirely - offering
# a variant part "Sway" is offering it a number its format cannot hold.
_RMV2_VERTEX_FORMATS = (
    "AUTO", "STATIC", "WEIGHTED", "CINEMATIC", "POSITION16",
    "POSITION_HALF", "POSITION_UV", "GRASS", "TREE_BILLBOARD",
    "VEGETATION", "SWAY", "COLLISION", "S2_POSITION_UV", "S2_STATIC",
    "S2_STATIC_FLOAT", "S2_BOW_WAVE",
)
_VMPF_VERTEX_FORMATS = ("AUTO", "VMPF_SKINNED", "VMPF_RIGID")

_CONTAINER_VERTEX_FORMATS = {
    "RMV2": _RMV2_VERTEX_FORMATS,
    "VMPF": _VMPF_VERTEX_FORMATS,
    # ARM and VWM have no vertex-format field at all, so their objects
    # never draw the row (see _OBJECT_CAPS) and never reach here.
}

# Everything, in enum order. The fallback for an object whose container
# cannot be worked out - one not linked into a root yet, which is the
# state every importer builds its meshes in. Narrowing there would make
# assigning the format the file actually said raise.
ALL_VERTEX_FORMATS = _RMV2_VERTEX_FORMATS + _VMPF_VERTEX_FORMATS[1:]


def vertex_format_keys_for_root(root) -> tuple:
    """The vertex formats this model's container can store.

    Falls back to everything when there is no root - see
    ALL_VERTEX_FORMATS.
    """
    if root is None:
        return ALL_VERTEX_FORMATS
    container, _ = container_and_version(root.rmv2)
    return _CONTAINER_VERTEX_FORMATS.get(container, ALL_VERTEX_FORMATS)


def vertex_format_keys(obj) -> tuple:
    """The vertex formats this object's container can store."""
    return vertex_format_keys_for_root(root_of(obj))


def container_and_version(settings) -> tuple:
    """('RMV2', 8) for a root collection's Version.

    Falls back to RMV2 v8 for anything unrecognised, which is also what a
    collection flagged by hand starts as.
    """
    for container in ("RMV2", "ARM", "VMPF", "VWM"):
        version = props.format_version(settings, container)
        if version is not None:
            return container, int(version)
    return "RMV2", 8


def parent_root_of(collection):
    """The model root this collection is a LOD *of*, or None.

    Being a LOD is not a flag anyone sets - it is what a collection *is*
    when it sits inside a model root. There is one checkbox, "Model
    Root", and that makes the two states mutually exclusive for free: a
    collection that is itself a root is not a LOD of its parent, so the
    contradictory state cannot be reached rather than being guarded
    against.
    """
    import bpy

    if collection is None or collection.rmv2.is_rmv2_root:
        return None
    for candidate in bpy.data.collections:
        if candidate.rmv2.is_rmv2_root and collection in set(
                candidate.children):
            return candidate
    return None


def is_lod(collection) -> bool:
    """Whether this collection is one LOD of a model."""
    return parent_root_of(collection) is not None


def find_root(collection):
    """The model root a collection belongs to, or itself, or None."""
    if collection is None:
        return None
    if collection.rmv2.is_rmv2_root:
        return collection
    return parent_root_of(collection)


def root_of(obj):
    """The RMV2 root collection an object belongs to, or None."""
    for collection in obj.users_collection:
        root = find_root(collection)
        if root is not None:
            return root
    return None


def collection_caps(root) -> set:
    """What the root collection's own panel should offer."""
    if root is None:
        return set(_COLLECTION_CAPS["RMV2"])
    container, version = container_and_version(root.rmv2)
    caps = set(_COLLECTION_CAPS[container])
    if container == "RMV2":
        # The 28-byte LOD header arrived with v7; before that a LOD is a
        # mesh count, a couple of sizes, an offset and a camera distance,
        # with nowhere to put a quality level.
        if version >= 7:
            caps.add("quality_level")
        # Attachment points are part of the modern material block;
        # Shogun 2's fixed-width one has no room for them.
        if version < 5:
            caps.discard("attach_points")
    return caps


def object_caps(obj) -> set:
    """What one mesh object's panel should offer.

    Driven by the container its model came from, then narrowed by what
    the object itself is: an unrigged prop in a `.variant_weighted_mesh`
    is an attachment, which *is* an ARM object and so does have texture
    slots, and a Shogun 2 RMV2 mesh loses most of the modern material.
    """
    root = root_of(obj)
    container, version = ("RMV2", 8) if root is None else \
        container_and_version(root.rmv2)

    if container == "VWM" and not obj.vertex_groups:
        # An attachment: an .animatable_rigid_model object in all but
        # name, written by the ARM exporter.
        container = "ARM"

    caps = set(_OBJECT_CAPS[container])
    if container == "RMV2" and version in (1, 2, 3):
        # Shogun 2's material is a run of fixed-width strings: a model
        # name, a texture set or two, and sometimes a bone index. There
        # is no room in it for anything else, and the exporter writes
        # none of it (export_rmv2._shogun2_material_from_settings).
        caps -= {"alpha_mode", "filters", "parent_matrix_index",
                 "texture_directory"}
        if version < 2:
            caps.discard("shader_name")
    elif container == "ARM" and version < armf._PARAMS_FROM_VERSION:
        # Object versions 0-3 have no parameter block at all: the
        # texture names are followed straight by the vertex count.
        caps.discard("shader_params")
    return caps
