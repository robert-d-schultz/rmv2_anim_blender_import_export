"""Property groups holding Total War model metadata on Blender IDs.

Everything needed to round-trip a mesh lives either here (common, user
editable settings) or in the `extra_json` blob (rare/advanced fields that we
preserve verbatim: transform matrices, padding bytes, shader parameter
lists...).
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from . import rmv2_format as rf

# ---------------------------------------------------------------------------
# Enum item tables
# ---------------------------------------------------------------------------

VERTEX_FORMAT_ITEMS = [
    ("AUTO", "Auto", "Static if the mesh has no bone weights, otherwise "
     "Cinematic (4 influences)", 0),
    ("STATIC", "Static",
     "No bone weights (buildings, props). 2 UV channels", 1),
    ("WEIGHTED", "Weighted", "2 bone influences per vertex", 2),
    ("CINEMATIC", "Cinematic", "4 bone influences per vertex", 3),
    # Rome 2 and Attila's terrain, vegetation and decal layouts.  A mesh
    # imported as one of these keeps it, so a tree exports as a tree.
    ("POSITION16", "Position (float)",
     "Four float32 - position only, used by decals (16 bytes)", 4),
    ("POSITION_HALF", "Position (half)",
     "A half4 position and nothing else, under a terrain tile (8 bytes)", 5),
    ("POSITION_UV", "Position+UV",
     "Position and one UV, no tangent frame - water planes and the "
     "flat card a tree collapses to (12 bytes)", 6),
    ("GRASS", "Grass",
     "Position, float32 UVs and a byte tangent frame (28 bytes)", 7),
    ("TREE_BILLBOARD", "Tree Billboard",
     "Position, normal and UV as halves, plus four unused (28 bytes)", 8),
    ("VEGETATION", "Vegetation",
     "Trees and shrubs: a half tangent frame, a rest position and eight "
     "wind weights, the last two kept as point attributes (60 bytes)", 9),
    ("SWAY", "Sway",
     "Warhammer's wind-swayed props - hanging cloth, leaf cards, a "
     "tree's canopy: position, normal and UV as halves, and a colour "
     "whose alpha is the sway weight (20 bytes)", 10),
    ("COLLISION", "Collision",
     "Float32 position and normal, no UVs (24 bytes)", 11),
    # Shogun 2 only. Its materials have no vertex-format field - the game
    # infers the layout from the stride - so keeping the one a mesh was
    # imported with matters: its material expects that exact stride.
    ("S2_POSITION_UV", "Shogun 2 Position+UV",
     "Shogun 2: position and one UV only, no tangent frame (12 bytes)", 12),
    ("S2_STATIC", "Shogun 2 Static",
     "Shogun 2: like Static but with a single UV channel (28 bytes)", 13),
    ("S2_STATIC_FLOAT", "Shogun 2 Static (float)",
     "Shogun 2: Static with full float32 positions and UVs (44 bytes)", 14),
    ("S2_BOW_WAVE", "Shogun 2 Bow Wave",
     "Shogun 2 and Rome 2's ships: the bow_wave layout (24 bytes). Its "
     "second position channel - where the crest travels to - is kept as "
     "a point attribute", 15),
    # .variant_part_mesh only. Like the Shogun 2 entries above, the file
    # has no vertex-format field of its own - the layout is chosen by the
    # header - so the one a mesh was imported with is what should be
    # written back unless the user deliberately changes it.
    ("VMPF_SKINNED", "Variant Part Skinned",
     "Shogun 2 unit part: 2 weighted bone influences, each position "
     "stored in its own bone's space (48 bytes). Needs an armature", 16),
    ("VMPF_RIGID", "Variant Part Rigid",
     "Shogun 2 unit part: plain float32 model-space positions, no "
     "skinning (64 bytes) - equipment, crests and blank parts", 17),
]

# The .variant_part_mesh layouts are not RMV2 vertex formats, so they are
# deliberately absent from VERTEX_FORMAT_TO_INT below; export_rmv2 treats
# anything it does not recognise as Auto.
VMPF_FORMAT_IDS = ("VMPF_SKINNED", "VMPF_RIGID")

VERTEX_FORMAT_TO_INT = {
    "STATIC": rf.VF_STATIC,
    "WEIGHTED": rf.VF_WEIGHTED,
    "CINEMATIC": rf.VF_CINEMATIC,
    "POSITION16": rf.VF_POSITION16,
    "POSITION_HALF": rf.VF_POSITION_HALF,
    "POSITION_UV": rf.VF_POSITION_UV,
    "GRASS": rf.VF_GRASS,
    "TREE_BILLBOARD": rf.VF_TREE_BILLBOARD,
    "VEGETATION": rf.VF_VEGETATION,
    "SWAY": rf.VF_SWAY,
    "COLLISION": rf.VF_COLLISION,
    "S2_POSITION_UV": rf.VF_S2_POSITION_UV,
    "S2_STATIC": rf.VF_S2_STATIC_NO_UV2,
    "S2_STATIC_FLOAT": rf.VF_S2_STATIC_FLOAT,
    "S2_BOW_WAVE": rf.VF_S2_BOW_WAVE,
}
VERTEX_FORMAT_FROM_INT = {v: k for k, v in VERTEX_FORMAT_TO_INT.items()}


# Built per object rather than being one fixed list, so a
# `.variant_part_mesh` mesh is not offered "Sway" - see
# capabilities.vertex_format_keys for what the rule is and is not.
#
# Two hazards come with a callable `items`, and both are handled here:
#
# * Blender resolves a callable-driven enum by *number*, so every entry in
#   VERTEX_FORMAT_ITEMS carries a permanent one. Without them the same
#   stored number would mean a different format as soon as the list
#   changed shape, silently rewriting a mesh when its root's Version was
#   edited.
# * The returned list has to outlive the call or Blender frees the
#   strings and draws garbage, hence the cache - which is keyed by the
#   key tuple, so it is also what makes repeat draws cheap.
#
# The list is the container's, and a format the mesh already holds is
# pinned onto it only when it would otherwise be unreadable. That pin
# used to fire on every container change, which left a
# `.variant_part_mesh` layout stuck in an RMV2 model's dropdown for good;
# changing the Version now converts the meshes first (see
# _on_version_set), so all the pin still covers is a mesh moved between
# two models, which fires no callback at all.
_VERTEX_FORMAT_ITEM_CACHE = {}


def _vertex_format_items(self, context):
    from . import capabilities

    keys = capabilities.vertex_format_keys(self.id_data)
    # One case is left where a mesh holds a format its container cannot
    # write: it was moved between models, which fires no callback at all.
    # Pin it so it stays visible and readable rather than resolving
    # against a list it is not in. Changing the Version no longer reaches
    # here - _on_version_set has already converted those to Auto - so
    # this cannot leave a stale entry in the dropdown the way it used to.
    current = self.vertex_format_stored
    if current and current not in keys:
        keys = tuple(keys) + (current,)
    items = _VERTEX_FORMAT_ITEM_CACHE.get(keys)
    if items is None:
        by_id = {row[0]: row for row in VERTEX_FORMAT_ITEMS}
        items = [by_id[key] for key in keys if key in by_id]
        _VERTEX_FORMAT_ITEM_CACHE[keys] = items
    return items


def _lod_override_vertex_format_items(self, context):
    """Same list as the per-mesh dropdown, for an auto-LOD override row.

    `self.id_data` is the root collection the row belongs to, so the
    container is right there - no need to go looking for a root.
    """
    from . import capabilities

    keys = capabilities.vertex_format_keys_for_root(self.id_data)
    items = _VERTEX_FORMAT_ITEM_CACHE.get(keys)
    if items is None:
        by_id = {row[0]: row for row in VERTEX_FORMAT_ITEMS}
        items = [by_id[key] for key in keys if key in by_id]
        _VERTEX_FORMAT_ITEM_CACHE[keys] = items
    return items


def _on_lod_override_vertex_format_set(self, context):
    self.vertex_format_stored = self.vertex_format


def _on_vertex_format_set(self, context):
    """Mirror the enum into a plain string.

    The items callback needs to know the current value and cannot ask the
    enum for it, so this shadow is the one thing that does.
    """
    self.vertex_format_stored = self.vertex_format


def _material_id_items():
    items = [("AUTO", "Auto",
              "default_type for static meshes, weighted for skinned ones",
              0)]
    for mat_id in sorted(rf.MATERIAL_NAMES):
        name = rf.MATERIAL_NAMES[mat_id]
        items.append((name, f"{name} ({mat_id})",
                      f"ModelMaterialEnum value {mat_id}", mat_id + 1))
    items.append(("OTHER", "Other (raw id)",
                  "Unknown material id, uses the 'Raw Material Id' value",
                  20000))
    return items


MATERIAL_ID_ITEMS = _material_id_items()


def _texture_type_items():
    items = []
    for ttype in sorted(rf.TEXTURE_TYPE_NAMES):
        name = rf.TEXTURE_TYPE_NAMES[ttype]
        items.append((name, f"{name} ({ttype})",
                      f"TextureType value {ttype}", ttype))
    items.append(("OTHER", "Other (raw id)",
                  "Unknown texture type, uses the 'Raw Type' value", 10000))
    return items


TEXTURE_TYPE_ITEMS = _texture_type_items()

# Every exported material must carry these four texture types; whichever
# aren't set on an object fall back to these placeholder paths, so the game
# never renders a texture-less (pink/black) material. See
# ensure_default_textures() below for where they get filled in.
DEFAULT_TEXTURES = {
    27: "commontextures/default_base_colour.dds",   # BaseColour
    29: "commontextures/default_material_map.dds",  # MaterialMap
    1: "commontextures/default_normal.dds",          # Normal
    3: "commontextures/test_mask.dds",               # Mask
}

ALPHA_MODE_ITEMS = [
    ("NONE", "Not Set", "Do not write an alpha parameter"),
    ("OPAQUE", "Opaque", "Alpha int param = 0"),
    ("TRANSPARENT", "Alpha Blend/Test", "Alpha int param = 1"),
]

# Each container's own version list.  These are what that format's export
# dialog offers; the collection panel shows all four as one dropdown
# (FORMAT_VERSION_ITEMS below).
#
# The explicit numbers are the values Blender stores in the .blend, and
# the RMV2 rows keep the ones they had while this was an RMV2-only enum,
# so a scene saved by an older release still reads back as the version it
# was saved with.
VERSION_ITEMS = [
    ("1", "RMV2 v1", "Shogun 2 era (no shader name per mesh)", 0),
    ("2", "RMV2 v2", "Shogun 2 era", 1),
    ("3", "RMV2 v3", "Rome 2's 3D user-interface models, and the last "
     "version to use the Shogun 2 layout", 2),
    ("5", "RMV2 v5", "Rome 2's first version: UTF-16 strings throughout. "
     "AssetEditor reads this but will not write it", 3),
    ("6", "RMV2 v6", "Rome 2 / Attila era, and the bulk of Rome 2", 4),
    ("7", "RMV2 v7", "Warhammer 1 & 2 era", 5),
    ("8", "RMV2 v8", "Warhammer 3 and Three Kingdoms (vertex colours). Two out of "
     "three Warhammer 3 meshes are this version", 6),
]

# The .animatable_rigid_model / .rigid_model object versions.
ARM_VERSION_ITEMS = [
    ("5", "ARM v5", "The common vanilla object version", 0),
    ("4", "ARM v4", "Also occurs in vanilla", 1),
    ("3", "ARM v3", "Shogun 2 era; carries no material parameter block", 2),
    ("2", "ARM v2", "Three flagged texture names, no ao slot, and no "
     "second UV set", 3),
    ("1", "ARM v1 (Empire/Napoleon)", "One unflagged texture name and no "
     "second UV set", 4),
    ("0", "ARM Headerless (Empire)", "No per-object magic or version, and "
     "no vertex colour either - one vanilla file uses this", 5),
]

# The Shogun 2 .variant_part_mesh versions.  Version 1 does not exist.
VMPF_VERSION_ITEMS = [
    ("0", "VMPF v0", "The oldest layout: its skinned vertex is eight bytes "
     "shorter, with no tangent frame on the second influence", 0),
    ("2", "VMPF v2", "Fauna and horses", 1),
    ("3", "VMPF v3", "The common one - unit parts and equipment", 2),
]

# .variant_weighted_mesh has one real version plus the headerless form.
VWM_VERSION_ITEMS = [
    ("1", "VWM v1", "The shipping layout, with magic and material "
     "parameters", 0),
    ("0", "VWM Headerless", "No magic, version or parameter block - 15 "
     "vanilla unit meshes use it", 1),
]

# The .anim versions.  Every one this add-on can write; the reader also
# takes 1 and 4-8, which is a subset, so anything read back fits.
ANIM_VERSION_ITEMS = [
    ("0", "Anim v0 (Shogun 2, headerless)",
     "Shogun 2's second layout: no version field, three extra floats per "
     "bone per frame. Only campaign pieces use it - prefer v1", 0),
    ("1", "Anim v1 (Shogun 2)", "Shogun 2 era", 1),
    ("4", "Anim v4", "Rome 2's first version: UTF-16 strings, and every "
     "bone stored in every frame as float32", 2),
    ("5", "Anim v5", "Rome 2 era, and all but 31 of its animations", 3),
    ("6", "Anim v6", "Between Rome 2's v5 and Warhammer 2's v7 - but no "
     "vanilla file of any game uses it, so this is written from the "
     "versions either side of it and has never met real data", 4),
    ("7", "Anim v7", "Warhammer 1/2/3 era (the version AssetEditor and "
     "the games' modding pipelines expect)", 5),
    ("8", "Anim v8", "Warhammer 3 era, and three quarters of its "
     "animations. Each bone is packed at its own rate; AssetEditor reads "
     "this version but will not write it", 6),
]
ANIM_VERSION_IDS = {item[0] for item in ANIM_VERSION_ITEMS}


# One Version dropdown for the lot.  A root collection is one file and a
# file has exactly one version, so a "Version" field plus a separate box
# holding three other containers' versions was asking the user to fill in
# four answers to a question that only ever has one.  "RMV2" is read
# loosely here: it names this add-on's model panel, not the
# .rigid_model_v2 container specifically.
#
# RMV2's identifiers stay bare ("8") because they are also the strings
# the exporter's own version option uses, and because they are what
# older .blend files stored; the other containers prefix theirs.
_CONTAINERS = (
    ("RMV2", ".rigid_model_v2", VERSION_ITEMS, 0),
    ("ARM", ".animatable_rigid_model / .rigid_model", ARM_VERSION_ITEMS, 10),
    ("VMPF", ".variant_part_mesh", VMPF_VERSION_ITEMS, 20),
    ("VWM", ".variant_weighted_mesh", VWM_VERSION_ITEMS, 30),
)


def format_version_id(container: str, version) -> str:
    """The unified enum identifier for one container's version."""
    return str(version) if container == "RMV2" else f"{container}_{version}"


def _format_version_items():
    items = []
    for container, heading, table, base in _CONTAINERS:
        items.append(("", heading, ""))
        for ident, label, description, value in table:
            items.append((format_version_id(container, ident), label,
                          description, base + value))
    return items


FORMAT_VERSION_ITEMS = _format_version_items()
FORMAT_VERSION_IDS = {item[0] for item in FORMAT_VERSION_ITEMS if item[0]}


def set_format_version(settings, container: str, version) -> None:
    """Record the container and version a root collection was read from.

    A version with no entry here is ignored rather than raising: the
    reader is the authority on what it can load, and this list only has
    to describe what can be written.
    """
    ident = format_version_id(container, version)
    if ident not in FORMAT_VERSION_IDS:
        return
    settings.version = ident
    if container != "RMV2" and settings.lod_overrides:
        # scene_layout.new_root flags the collection before the importer
        # gets to say what it read, and flagging it auto-fills the four
        # RMV2 auto-LOD rows (_on_is_rmv2_root_update). Nothing but the
        # RMV2 exporter has a decimator behind those rows, so on a model
        # from another container they are four rows of nothing.
        settings.lod_overrides.clear()


def format_version(settings, container: str):
    """The stored version as a string if it belongs to `container`, else
    None.

    None means the model came from some other format, so that format's
    exporter should fall back to its own default rather than reading, say,
    an RMV2 version as a .variant_part_mesh one.
    """
    stored = settings.version
    if container == "RMV2":
        return None if "_" in stored else stored
    prefix = container + "_"
    return stored[len(prefix):] if stored.startswith(prefix) else None


def chosen_version(options, key: str, fallback) -> str:
    """`options[key]` if the caller set one, else `fallback`.

    Not `options.get(key) or fallback`: version 0 is a real version in
    four of the five containers here - the headerless .anim, the
    headerless .variant_weighted_mesh, VMPF v0 and ARM's headerless
    object - and `0 or x` is x.
    """
    value = options.get(key)
    return str(fallback if value is None else value)


def root_format_version(root, container: str, default: str) -> str:
    """The version to write for `container`, from a root collection.

    This is where every mesh exporter gets its version: the model records
    what it was read from, and that is the single answer, so there is no
    version field in any export dialog to disagree with it.  `default` is
    used for a model that came from a different container (or from
    nowhere - a fresh Blender mesh with no root collection).
    """
    if root is not None and root.rmv2.is_rmv2_root:
        value = format_version(root.rmv2, container)
        if value is not None:
            return value
    return default


def set_anim_version(settings, version) -> None:
    """Record a .anim version on an armature, ignoring one with no entry."""
    if str(version) in ANIM_VERSION_IDS:
        settings.anim_version = str(version)


# ---------------------------------------------------------------------------
# Property groups
# ---------------------------------------------------------------------------

class RMV2TextureSlot(bpy.types.PropertyGroup):
    texture_type: EnumProperty(
        name="Type", items=TEXTURE_TYPE_ITEMS, default="BaseColour",
        description="Texture slot type (TextureType enum in the file)")
    raw_type: IntProperty(
        name="Raw Type", default=0, min=0,
        description="Numeric texture type, used when Type is 'Other'")
    path: StringProperty(
        name="Path", default="",
        description="Pack-file relative path, e.g. "
        "variantmeshes\\wh_variantmodels\\...\\body_base_colour.dds")

    def type_as_int(self) -> int:
        if self.texture_type == "OTHER":
            return self.raw_type
        return _texture_name_to_int(self.texture_type)

    def set_type_from_int(self, value: int):
        if value in rf.TEXTURE_TYPE_NAMES:
            self.texture_type = rf.TEXTURE_TYPE_NAMES[value]
        else:
            self.texture_type = "OTHER"
            self.raw_type = value


def _texture_name_to_int(name: str) -> int:
    for ttype, tname in rf.TEXTURE_TYPE_NAMES.items():
        if tname == name:
            return ttype
    return 0


class RMV2ObjectSettings(bpy.types.PropertyGroup):
    model_name: StringProperty(
        name="Model Name Override", default="", maxlen=31,
        description="Mesh name stored in the file (max 31 chars). "
        "Empty = use the object name")
    vertex_format: EnumProperty(
        name="Vertex Format", items=_vertex_format_items,
        update=_on_vertex_format_set,
        description="How this mesh's vertices are laid out in the file. "
        "Per mesh, not per file - vanilla models mix them freely, a "
        "Warhammer 3 unit being Cinematic up close and Weighted at "
        "distance. The list is narrowed to what the model's container "
        "can store, never to what its version usually stores")
    vertex_format_stored: StringProperty(
        default="",
        description="Internal: the vertex format as a plain string. The "
        "dropdown is built per object, and building it needs the current "
        "value without asking the enum for it")
    material_id: EnumProperty(
        name="Material", items=MATERIAL_ID_ITEMS, default="AUTO",
        description="ModelMaterialEnum written to the mesh header")
    material_id_raw: IntProperty(
        name="Raw Material Id", default=68, min=0, max=65535,
        description="Numeric material id, used when Material is 'Other'")
    alpha_mode: EnumProperty(
        name="Alpha Mode", items=ALPHA_MODE_ITEMS, default="OPAQUE")
    render_flag: IntProperty(
        name="Render Flag", default=0, min=0, max=65535)
    shader_name: StringProperty(
        name="Shader", default=rf.DEFAULT_SHADER_NAME, maxlen=12)
    texture_directory: StringProperty(
        name="Texture Directory", default="",
        description="Pack-file relative texture directory")
    filters: StringProperty(
        name="Filters", default="",
        description="Filter string from the material header (usually empty)")
    matrix_index: IntProperty(
        name="Bone Index", default=-1, soft_min=-1, soft_max=255,
        description="The single bone this mesh rides whole rather than "
        "deforming against - a destructible building's chunk, a cannon "
        "barrel, a helmet, a musket. -1 = none. Once the mesh is welded "
        "to an armature this is cleared and the Child Of constraint is "
        "what says which bone, so re-target that to move the piece. "
        "Called the matrix index in .rigid_model_v2's own header")
    # Identified by sweeping all 23 618 vanilla Warhammer 3 models: the
    # 81 meshes that set it are every one a `collider_*` mesh in a
    # `*_cloth_cloak_01`, and 58 of them are named after a body part -
    # all 58 naming exactly the bone their value points at. See
    # docs/FORMATS.md.
    parent_matrix_index: IntProperty(
        name="Collider Bone", default=-1, soft_min=-1, soft_max=255,
        description="For a cloth-physics collider proxy, the bone it "
        "follows; -1 = not a collider. The field the .rigid_model_v2 "
        "header calls the parent matrix index. Every vanilla mesh that "
        "sets it is a collider in a cloth cloak, with its own Bone Index "
        "left at -1")
    textures: CollectionProperty(type=RMV2TextureSlot)
    active_texture_index: IntProperty(default=0)
    textures_initialized: BoolProperty(
        default=False,
        description="Internal: whether the 4 default texture slots have "
        "already been filled in or deliberately edited, so they don't get "
        "re-added")
    shader_params: CollectionProperty(type=RMV2ShaderParam)
    active_shader_param_index: IntProperty(default=0)
    shader_params_initialized: BoolProperty(
        default=False,
        description="Internal: whether this object's parameter list came "
        "from a file. An imported object with no parameters keeps none; "
        "one built in Blender gets the format's defaults")
    material_names: CollectionProperty(type=RMV2MaterialName)
    material_names_initialized: BoolProperty(default=False)
    extra_json: StringProperty(
        name="Extra Data (JSON)", default="",
        description="Preserved raw fields: transform matrices, padding, "
        "shader bytes and string/float/int/vec4 parameter lists")


def ensure_default_textures(settings: RMV2ObjectSettings) -> None:
    """Fill in whichever of the 4 core texture types (BaseColour,
    MaterialMap, Normal, Mask) are missing from `settings.textures` with
    DEFAULT_TEXTURES, once per object - a starting point for a brand new
    mesh (never imported), shown as ordinary editable slots in the RMV2
    panel (see ui.py's OBJECT_PT_rmv2.draw). Deliberately *not* called from
    the exporter: not every Total War game even uses BaseColour, so
    silently writing these paths into a file the user hasn't looked at
    would be wrong for some games and invisible either way - if a mesh's
    textures list is incomplete at export time, that's exported as-is.
    Also not used for imported objects - those keep exactly what the RMV2
    file had; see import_rmv2._fill_object_settings, which sets
    textures_initialized itself instead of calling this. A no-op once
    `textures_initialized` is set, so deliberately clearing a slot later
    doesn't bring the default back."""
    if settings.textures_initialized:
        return
    present = {slot.type_as_int() for slot in settings.textures}
    for ttype, path in DEFAULT_TEXTURES.items():
        if ttype not in present:
            slot = settings.textures.add()
            slot.set_type_from_int(ttype)
            slot.path = path
    settings.textures_initialized = True


class RMV2MaterialName(bpy.types.PropertyGroup):
    """One of a `.variant_part_mesh` part's three material names.

    That format has no texture slots and no material id - three names is
    the whole of what it says about a surface, and vanilla writes
    "default" in all three. A library file (vertex format 2) keeps them
    per part; the others keep one set for the file, which is why both the
    object and the collection settings have a list of them.
    """

    name: StringProperty(name="Material", default="default")


def fill_material_names(settings, names) -> None:
    """Replace `settings.material_names` with what a file held."""
    settings.material_names.clear()
    for value in names:
        settings.material_names.add().name = value
    settings.material_names_initialized = True


def read_material_names(settings, count=3, fallback="default") -> list:
    """Exactly `count` names, padded - the field is fixed width."""
    names = [entry.name for entry in settings.material_names]
    return (names + [fallback] * count)[:count]


class RMV2ShaderParam(bpy.types.PropertyGroup):
    """One named shader parameter - "light_scale", "specfactor".

    Shogun 2 and Empire's containers carry a block of these: a count,
    then name/value pairs, floats first and four-component vectors
    after. They are the whole of the material in the formats that have
    no material id, so CA tunes them per model rather than leaving them
    at a default.

    *Where* the block sits differs by container, which is why both the
    object and the collection settings have a list of these and
    capabilities.py decides which panel draws it:

    * `.animatable_rigid_model` / `.rigid_model` - one block per object,
      from object version 4. Objects in one file genuinely differ:
      Shogun 2's naval_cannon_12lb_lod4 has 13 parameters on three of
      its four objects and none at all on the other.
    * `.variant_part_mesh` and `.variant_weighted_mesh` - one block for
      the whole file, before the parts.
    * `.rigid_model_v2` addresses its parameters by index rather than by
      name, so they are not these; they live in `extra_json`.

    The second list is four-component, and every parameter in it across
    the sample corpus is an RGBA colour - `colourmapfactor`, `rimcolor`,
    `specfactor` - so that is how it is drawn. See `vector` below.
    """

    name: StringProperty(
        name="Name", default="",
        description="Parameter name as the shader reads it, e.g. "
        "light_scale, bumpfactor, specfactor")
    kind: EnumProperty(
        name="Type", default="FLOAT",
        items=[("FLOAT", "Float", "A single value"),
               ("VEC4", "Colour", "Four values, RGBA - every parameter "
                "in the file's second list is a colour")],
        description="Which of the file's two parameter lists this "
        "belongs to. The file keeps them apart, so this is not a display "
        "choice")
    value: FloatProperty(name="Value", default=0.0)
    # Every four-component parameter in the corpus is an RGBA colour -
    # colourmapfactor, rimcolor, specfactor - so it gets a colour swatch
    # rather than four anonymous numbers.
    #
    # COLOR_GAMMA, not COLOR: the swatch then reads as the numbers the
    # file stores, and these are DX9-era shader factors from before
    # anyone shipped a linear pipeline, so treating them as linear light
    # would both misdraw them and imply something we cannot check.
    #
    # soft_min/soft_max, never min/max: a hard range would silently clamp
    # a file whose values fall outside it (verified - a hard 0..1 turns
    # 2.5 into 1.0 on assignment). Nothing in the corpus leaves 0..1, but
    # the reader must not be the thing that decides that.
    vector: FloatVectorProperty(
        name="Colour", size=4, subtype="COLOR_GAMMA",
        soft_min=0.0, soft_max=1.0, default=(0.0, 0.0, 0.0, 0.0),
        description="RGBA value. The swatch shows the stored numbers "
        "directly; open it or use the fields below to type exact ones")


def fill_shader_params(settings, float_params, vec4_params) -> None:
    """Replace `settings.shader_params` with what a file held.

    Called by every importer whose container has a parameter block, and
    it marks the list as filled even when the block was empty - an
    object with no parameters is a thing vanilla files do, and it has to
    survive re-export as empty rather than being handed CA's defaults.
    """
    settings.shader_params.clear()
    for name, value in float_params:
        entry = settings.shader_params.add()
        entry.name = name
        entry.kind = "FLOAT"
        entry.value = float(value)
    for name, value in vec4_params:
        entry = settings.shader_params.add()
        entry.name = name
        entry.kind = "VEC4"
        entry.vector = tuple(float(v) for v in value)
    settings.shader_params_initialized = True
    settings.active_shader_param_index = 0


def read_shader_params(settings) -> tuple:
    """(float params, vec4 params) as the format modules want them.

    Order within each list is the panel's order, so moving a row moves
    it in the file. Unnamed rows are dropped: the file has no way to
    write one.
    """
    floats, vec4s = [], []
    for entry in settings.shader_params:
        if not entry.name:
            continue
        if entry.kind == "VEC4":
            vec4s.append((entry.name, tuple(entry.vector)))
        else:
            floats.append((entry.name, float(entry.value)))
    return floats, vec4s


def shader_params_or_default(settings, defaults) -> tuple:
    """What to write for a model that may never have been imported.

    An imported model uses exactly what its file had, empty included.
    Anything else - a mesh built in Blender - gets the block CA writes
    for a plain lit object, because a file with no parameters at all
    renders unlit in these games.
    """
    if settings.shader_params_initialized:
        return read_shader_params(settings)
    floats, vec4s = defaults
    return list(floats), list(vec4s)


class RMV2AttachPoint(bpy.types.PropertyGroup):
    """One attachment point (name + bone index + 3x4 rest matrix), stored
    on the root collection and re-written on export."""
    name: StringProperty(
        name="Name", default="",
        description="Attachment point name, usually a bone name")
    bone_index: IntProperty(
        name="Bone Index", default=0, min=0, max=65535,
        description="Skeleton bone index this attachment point follows")
    matrix: bpy.props.FloatVectorProperty(
        name="Matrix", size=12,
        default=rf.IDENTITY_3X4,
        description="3x4 row-major rest matrix from the file (game space)")


class RMV2LodOverride(bpy.types.PropertyGroup):
    """One row of the auto-LOD ('Generate LODs') override list: the
    settings LOD `n` (row index n) should get when the exporter expands a
    single mesh set into several LODs. See export_rmv2.expand_auto_lods."""
    vertex_format: EnumProperty(
        name="Vertex Format", items=_lod_override_vertex_format_items,
        update=_on_lod_override_vertex_format_set,
        description="Override the vertex format for this LOD (e.g. force "
        "farther LODs of a rigged model to Weighted instead of Cinematic). "
        "Auto = keep each mesh's own object setting. Narrowed to what the "
        "model's container can store, like the per-mesh setting")
    vertex_format_stored: StringProperty(
        default="",
        description="Internal: this row's vertex format as a plain "
        "string - see RMV2ObjectSettings.vertex_format_stored")
    decimate_ratio: FloatProperty(
        name="Decimate Ratio", default=1.0, min=0.0, max=1.0,
        description="Triangle ratio kept by the Decimate modifier "
        "generating this LOD from LOD0 (1.0 = unmodified)")
    camera_distance: FloatProperty(
        name="Camera Distance", default=40.0,
        description="Distance until which this LOD is displayed")
    quality_level: IntProperty(
        name="Quality Level", default=0, min=0, max=255,
        description="Lowest graphics quality level at which this LOD is "
        "active (0 = visible on all settings)")


def _on_version_set(self, context):
    """Changing the Version can change the *container*, and a vertex
    format the old one used may be a number the new one cannot hold - a
    `.variant_part_mesh` layout is not something `.rigid_model_v2` can
    write.

    So a format that is no longer valid is converted to Auto, which is
    the exporter working it out from the mesh. Formats that are valid in
    both are untouched, which is every one of them when the version moves
    inside a container (v7 to v8 changes nothing here).

    This asks each mesh whether its own stored format still fits, rather
    than tracking what the container used to be - no extra state to get
    out of step, and it is correct the first time it runs.
    """
    from . import capabilities

    if not self.is_rmv2_root:
        return
    root = self.id_data
    keys = set(capabilities.vertex_format_keys_for_root(root))
    for obj in root.all_objects:
        if obj.type != "MESH":
            continue
        settings = obj.rmv2
        stored = settings.vertex_format_stored
        if stored and stored not in keys:
            settings.vertex_format = "AUTO"
    for row in self.lod_overrides:
        stored = row.vertex_format_stored
        if stored and stored not in keys:
            row.vertex_format = "AUTO"


def _on_is_rmv2_root_update(self, context):
    """Auto-fill the 4 default auto-LOD override rows the moment a
    collection is flagged as an RMV2 root, same as what RMV2 import does -
    so a from-scratch collection doesn't start with an empty, useless
    override list that the user has to know to fill via the reset button."""
    if not self.is_rmv2_root or self.lod_overrides:
        return
    from .export_rmv2 import default_lod_overrides
    from .ui import _collection_is_rigged
    collection = self.id_data
    default_lod_overrides(collection, _collection_is_rigged(collection))


class RMV2CollectionSettings(bpy.types.PropertyGroup):
    is_rmv2_root: BoolProperty(
        name="Model Root", default=False,
        description="This collection is one Total War model file - a "
        ".rigid_model_v2, .animatable_rigid_model, .variant_part_mesh or "
        ".variant_weighted_mesh. Which one is the Version below",
        update=_on_is_rmv2_root_update)
    version: EnumProperty(
        name="Version", items=FORMAT_VERSION_ITEMS, default="8",
        update=_on_version_set,
        description="Which file this model was read from, and which "
        "version of it. Every exporter opens on this; one for a different "
        "container opens on that container's default instead. A model "
        "built from scratch starts at the version Warhammer 3 most "
        "commonly ships")
    skeleton_name: StringProperty(
        name="Skeleton", default="",
        description="Skeleton name written to the file header, e.g. "
        "humanoid01. Leave empty for static models")
    attach_points: CollectionProperty(type=RMV2AttachPoint)
    active_attach_index: IntProperty(default=0)
    # There is deliberately no "is LOD" flag: a collection is a LOD when
    # it sits inside a model root, which capabilities.is_lod derives. Two
    # flags meant the contradictory "root and LOD" state existed and had
    # to be guarded; one checkbox means it cannot happen.
    lod_level: IntProperty(
        name="LOD Level", default=0, min=0,
        description="Which level of the ladder this collection is. Left "
        "at 0 on a collection named like '..._lod2', the name is used "
        "instead - so a ladder built by hand works without touching this")
    camera_distance: FloatProperty(
        name="Camera Distance", default=40.0,
        description="Distance until which this LOD is displayed")
    quality_level: IntProperty(
        name="Quality Level", default=0, min=0, max=255,
        description="Lowest graphics quality level at which this LOD is "
        "active (0 = visible on all settings)")
    lod_values_set: BoolProperty(
        default=False,
        description="Internal: whether Camera Distance and Quality Level "
        "here are real - read from a file, or filled in by Setup LOD "
        "Collections. A ladder built by hand gets the era's defaults on "
        "export instead of these properties' own")
    lod_overrides: CollectionProperty(type=RMV2LodOverride)
    active_lod_override_index: IntProperty(default=0)
    # .variant_part_mesh and .variant_weighted_mesh keep one parameter
    # block for the whole file, so theirs belongs to the root collection
    # rather than to any one mesh. See RMV2ShaderParam.
    shader_params: CollectionProperty(type=RMV2ShaderParam)
    active_shader_param_index: IntProperty(default=0)
    shader_params_initialized: BoolProperty(
        default=False,
        description="Internal: whether this model's parameter list came "
        "from a file")
    material_names: CollectionProperty(type=RMV2MaterialName)
    material_names_initialized: BoolProperty(default=False)


class RMV2ArmatureSettings(bpy.types.PropertyGroup):
    """.anim metadata for an armature, stored on the Armature data-block
    (Object Data Properties tab) instead of raw custom properties so it
    reads like the model/collection panels rather than cluttering
    the generic Custom Properties list."""
    skeleton_name: StringProperty(
        name="Skeleton", default="",
        description="Skeleton name from the .anim header this armature "
        "was built from (e.g. humanoid01, or 'building' for ad-hoc "
        "animations). Animations imported onto this armature are checked "
        "against this name")
    anim_version: EnumProperty(
        name="Anim Version", items=ANIM_VERSION_ITEMS, default="8",
        description="The .anim version this armature was read from, and "
        "the one it is written back as. Export takes it from here - "
        "there is no version field in the export dialog - so change it "
        "here to move an animation between games")
    anim_fps: FloatProperty(
        name="Frame Rate", default=20.0, min=0.0,
        description="Frame rate from the .anim header; used as the "
        "export default")
    anim_header_type: IntProperty(
        name="Header Type", default=1, min=0, max=255,
        description="The u32 after the version in the .anim header. 1 in "
        "almost every file; a handful of Warhammer 3 animations carry 2 "
        "or 0. Its meaning is unknown, so it is preserved rather than "
        "assumed")
    flags: StringProperty(
        name="Flags", default="",
        description="Comma-separated v7+ animation flag strings (rare, "
        "e.g. shake_camera), preserved for re-export")
    has_event_block: BoolProperty(
        name="Event Block", default=False,
        description="Whether the file ends with an event block. Rome 2's "
        "v4 and v5 write an empty one and Shogun 2 fills it in; dropping "
        "it on export made the file shorter than it came in")
    extra_event_blocks_json: StringProperty(
        name="Extra Event Blocks", default="",
        description="Further event blocks after the first, as JSON. "
        "Preserved for re-export")
    events_json: StringProperty(
        name="Events", default="",
        description="Shogun 2 animation events (FIRE_TIME, OFF_BONE1, "
        "FIRE_POSITION...) as JSON, preserved for re-export. Shogun 2 "
        "stores these in the .anim itself rather than a separate table")


class RMV2AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    texture_root: StringProperty(
        name="Texture Root Directory", subtype="DIR_PATH", default="",
        description="Folder containing extracted game textures. Texture "
        "paths from RMV2 files are resolved relative to this directory "
        "when building Blender materials")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "texture_root")
        layout.label(
            text="Extract textures from the game packs (e.g. with RPFM or "
            "AssetEditor) into this folder to see them on import.",
            icon="INFO")


def get_texture_root(context) -> str:
    try:
        prefs = context.preferences.addons[__package__].preferences
        return prefs.texture_root or ""
    except (KeyError, AttributeError):
        return ""


CLASSES = (
    RMV2TextureSlot,
    RMV2MaterialName,
    RMV2ShaderParam,
    RMV2ObjectSettings,
    RMV2AttachPoint,
    RMV2LodOverride,
    RMV2CollectionSettings,
    RMV2ArmatureSettings,
    RMV2AddonPreferences,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.rmv2 = bpy.props.PointerProperty(
        type=RMV2ObjectSettings)
    bpy.types.Collection.rmv2 = bpy.props.PointerProperty(
        type=RMV2CollectionSettings)
    bpy.types.Armature.rmv2 = bpy.props.PointerProperty(
        type=RMV2ArmatureSettings)


def unregister():
    del bpy.types.Armature.rmv2
    del bpy.types.Collection.rmv2
    del bpy.types.Object.rmv2
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
