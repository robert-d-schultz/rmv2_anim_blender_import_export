"""Property groups holding RMV2 metadata on Blender IDs.

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
    IntProperty,
    StringProperty,
)

from . import rmv2_format as rf

# ---------------------------------------------------------------------------
# Enum item tables
# ---------------------------------------------------------------------------

VERTEX_FORMAT_ITEMS = [
    ("AUTO", "Auto", "Static if the mesh has no bone weights, otherwise "
     "Cinematic (4 influences)"),
    ("STATIC", "Static", "No bone weights (buildings, props). 2 UV channels"),
    ("WEIGHTED", "Weighted", "2 bone influences per vertex"),
    ("CINEMATIC", "Cinematic", "4 bone influences per vertex"),
]

VERTEX_FORMAT_TO_INT = {
    "STATIC": rf.VF_STATIC,
    "WEIGHTED": rf.VF_WEIGHTED,
    "CINEMATIC": rf.VF_CINEMATIC,
}
VERTEX_FORMAT_FROM_INT = {v: k for k, v in VERTEX_FORMAT_TO_INT.items()}


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

VERSION_ITEMS = [
    ("6", "RMV2 v6", "Rome 2 / Attila era"),
    ("7", "RMV2 v7", "Warhammer 1 & 2 era"),
    ("8", "RMV2 v8", "Warhammer 3 / Troy era (vertex colours)"),
]


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
        name="Vertex Format", items=VERTEX_FORMAT_ITEMS, default="AUTO")
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
        name="Matrix Index", default=-1, soft_min=-1, soft_max=255,
        description="Bone/matrix the mesh is attached to (destructible "
        "buildings etc.), -1 = none")
    parent_matrix_index: IntProperty(
        name="Parent Matrix Index", default=-1, soft_min=-1, soft_max=255)
    textures: CollectionProperty(type=RMV2TextureSlot)
    active_texture_index: IntProperty(default=0)
    textures_initialized: BoolProperty(
        default=False,
        description="Internal: whether the 4 default texture slots have "
        "already been filled in or deliberately edited, so they don't get "
        "re-added")
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
        name="Vertex Format", items=VERTEX_FORMAT_ITEMS, default="AUTO",
        description="Override the vertex format for this LOD (e.g. force "
        "farther LODs of a rigged model to Weighted instead of Cinematic). "
        "Auto = keep each mesh's own object setting")
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
        name="RMV2 Root", default=False,
        description="This collection represents one .rigid_model_v2 file",
        update=_on_is_rmv2_root_update)
    version: EnumProperty(
        name="Version", items=VERSION_ITEMS, default="7")
    skeleton_name: StringProperty(
        name="Skeleton", default="",
        description="Skeleton name written to the file header, e.g. "
        "humanoid01. Leave empty for static models")
    attach_points: CollectionProperty(type=RMV2AttachPoint)
    active_attach_index: IntProperty(default=0)
    is_lod: BoolProperty(
        name="RMV2 LOD", default=False,
        description="This collection is one LOD of an RMV2 model")
    lod_level: IntProperty(name="LOD Level", default=0, min=0)
    camera_distance: FloatProperty(
        name="Camera Distance", default=40.0,
        description="Distance until which this LOD is displayed")
    quality_level: IntProperty(
        name="Quality Level", default=0, min=0, max=255,
        description="Lowest graphics quality level at which this LOD is "
        "active (0 = visible on all settings)")
    lod_overrides: CollectionProperty(type=RMV2LodOverride)
    active_lod_override_index: IntProperty(default=0)


class RMV2ArmatureSettings(bpy.types.PropertyGroup):
    """.anim metadata for an armature, stored on the Armature data-block
    (Object Data Properties tab) instead of raw custom properties so it
    reads like the model/collection RMV2 panels rather than cluttering
    the generic Custom Properties list."""
    skeleton_name: StringProperty(
        name="Skeleton", default="",
        description="Skeleton name from the .anim header this armature "
        "was built from (e.g. humanoid01, or 'building' for ad-hoc "
        "animations). Animations imported onto this armature are checked "
        "against this name")
    anim_version: IntProperty(
        name="Anim Version", default=7, min=0, max=8,
        description="The .anim format version last imported/exported for "
        "this armature; used as the export default")
    anim_fps: FloatProperty(
        name="Frame Rate", default=20.0, min=0.0,
        description="Frame rate from the .anim header; used as the "
        "export default")
    flags: StringProperty(
        name="Flags", default="",
        description="Comma-separated v7+ animation flag strings (rare, "
        "e.g. shake_camera), preserved for re-export")


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
