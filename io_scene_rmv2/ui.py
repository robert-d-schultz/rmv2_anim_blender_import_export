"""UI: the Total War panels in the Object, Collection and Object Data
property tabs, the texture slot list, and small workflow operators.

The panels are titled "Total War Settings" because they cover every
mesh-like format this add-on reads, not only .rigid_model_v2 - which
row of fields any one of them shows comes from `capabilities`. The
internal name stays `rmv2` (`obj.rmv2`, `is_rmv2_root`, the operator
ids): those are in saved .blend files and in every script that drives
this add-on, and renaming them would break both for a cosmetic gain."""

from __future__ import annotations

import re

import bpy
from bpy.props import IntProperty

from . import capabilities
from .properties import ensure_default_textures

# The title every one of these panels carries. They live in different
# property tabs, so they are never on screen together.
PANEL_TITLE = "Total War Settings"

# ---------------------------------------------------------------------------
# Deferred default-texture fill
# ---------------------------------------------------------------------------

# Blender forbids mutating ID data (like adding to a CollectionProperty)
# from inside a Panel.draw() callback - objects whose textures were never
# initialized (anything imported before textures_initialized existed, or a
# native never-imported mesh) would raise "Writing to ID classes in this
# context is not allowed" the moment their RMV2 panel was drawn, leaving it
# empty. Defer the actual write to a timer, which runs outside the
# draw-callback's read-only context.
_pending_default_texture_fills = set()


def _defer_ensure_default_textures(obj_name):
    if obj_name in _pending_default_texture_fills:
        return

    def _do_fill():
        _pending_default_texture_fills.discard(obj_name)
        obj = bpy.data.objects.get(obj_name)
        if obj is not None and obj.type == "MESH":
            ensure_default_textures(obj.rmv2)
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == "PROPERTIES":
                        area.tag_redraw()
        return None

    _pending_default_texture_fills.add(obj_name)
    bpy.app.timers.register(_do_fill)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


class RMV2_OT_texture_add(bpy.types.Operator):
    bl_idname = "rmv2.texture_add"
    bl_label = "Add Texture Slot"
    bl_description = "Add a texture slot to the active object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        s = context.object.rmv2
        s.textures.add()
        s.active_texture_index = len(s.textures) - 1
        return {"FINISHED"}


class RMV2_OT_texture_remove(bpy.types.Operator):
    bl_idname = "rmv2.texture_remove"
    bl_label = "Remove Texture Slot"
    bl_description = "Remove the selected texture slot"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.object is not None
                and len(context.object.rmv2.textures) > 0)

    def execute(self, context):
        s = context.object.rmv2
        index = min(s.active_texture_index, len(s.textures) - 1)
        s.textures.remove(index)
        s.active_texture_index = max(0, index - 1)
        return {"FINISHED"}


class RMV2_OT_attach_add(bpy.types.Operator):
    bl_idname = "rmv2.attach_add"
    bl_label = "Add Attachment Point"
    bl_description = "Add an attachment point to the active collection"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.collection is not None

    def execute(self, context):
        s = context.collection.rmv2
        entry = s.attach_points.add()
        entry.name = f"bone_{len(s.attach_points) - 1}"
        entry.bone_index = len(s.attach_points) - 1
        s.active_attach_index = len(s.attach_points) - 1
        return {"FINISHED"}


class RMV2_OT_attach_remove(bpy.types.Operator):
    bl_idname = "rmv2.attach_remove"
    bl_label = "Remove Attachment Point"
    bl_description = "Remove the selected attachment point"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.collection is not None
                and len(context.collection.rmv2.attach_points) > 0)

    def execute(self, context):
        s = context.collection.rmv2
        index = min(s.active_attach_index, len(s.attach_points) - 1)
        s.attach_points.remove(index)
        s.active_attach_index = max(0, index - 1)
        return {"FINISHED"}


def _shader_param_owner(context):
    """Whose parameter list the buttons act on.

    The block is per object in `.animatable_rigid_model` and per file in
    the two variant formats, so the same two buttons serve both - the
    panel that drew them says which. See capabilities.py.
    """
    collection = context.collection
    if (collection is not None and collection.rmv2.is_rmv2_root
            and "shader_params" in capabilities.collection_caps(collection)):
        return collection.rmv2
    if context.object is not None and context.object.type == "MESH":
        return context.object.rmv2
    return None


class RMV2_OT_shader_param_add(bpy.types.Operator):
    bl_idname = "rmv2.shader_param_add"
    bl_label = "Add Shader Parameter"
    bl_description = ("Add a named shader parameter. The name has to be "
                      "one the game's shader reads - there is no list of "
                      "them in the file")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _shader_param_owner(context) is not None

    def execute(self, context):
        s = _shader_param_owner(context)
        entry = s.shader_params.add()
        entry.name = "light_scale"
        entry.value = 1.0
        # Adding one by hand means this list is now deliberate, so the
        # exporter must not fall back to the format's defaults.
        s.shader_params_initialized = True
        s.active_shader_param_index = len(s.shader_params) - 1
        return {"FINISHED"}


class RMV2_OT_shader_param_remove(bpy.types.Operator):
    bl_idname = "rmv2.shader_param_remove"
    bl_label = "Remove Shader Parameter"
    bl_description = "Remove the selected shader parameter"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = _shader_param_owner(context)
        return s is not None and len(s.shader_params) > 0

    def execute(self, context):
        s = _shader_param_owner(context)
        index = min(s.active_shader_param_index, len(s.shader_params) - 1)
        s.shader_params.remove(index)
        # An emptied list is a choice too: vanilla files do ship objects
        # with no parameters at all.
        s.shader_params_initialized = True
        s.active_shader_param_index = max(0, index - 1)
        return {"FINISHED"}


class RMV2_OT_lod_override_add(bpy.types.Operator):
    bl_idname = "rmv2.lod_override_add"
    bl_label = "Add LOD Override"
    bl_description = ("Add a row to the active collection's auto-LOD "
                      "override list (used by Export > Generate LODs)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.collection is not None

    def execute(self, context):
        from .export_rmv2 import default_camera_distance
        s = context.collection.rmv2
        _, version = capabilities.container_and_version(s)
        entry = s.lod_overrides.add()
        level = len(s.lod_overrides) - 1    # row index == LOD level
        entry.decimate_ratio = 1.0 if level == 0 else 0.5 ** level
        entry.camera_distance = default_camera_distance(
            level, version, len(s.lod_overrides))
        s.active_lod_override_index = level
        return {"FINISHED"}


class RMV2_OT_lod_override_remove(bpy.types.Operator):
    bl_idname = "rmv2.lod_override_remove"
    bl_label = "Remove LOD Override"
    bl_description = "Remove the selected auto-LOD override row"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.collection is not None
                and len(context.collection.rmv2.lod_overrides) > 0)

    def execute(self, context):
        s = context.collection.rmv2
        index = min(s.active_lod_override_index, len(s.lod_overrides) - 1)
        s.lod_overrides.remove(index)
        s.active_lod_override_index = max(0, index - 1)
        return {"FINISHED"}


def _collection_is_rigged(collection) -> bool:
    """Best-effort guess for the default-fill below: does any mesh in this
    collection (recursively, LODs included) look bone-weighted - an
    armature modifier/parent, or a bone_<i>-style vertex group."""
    bone_group = re.compile(r"^(bn_|bone_\d+$)")
    for obj in collection.all_objects:
        if obj.type != "MESH":
            continue
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.object is not None:
                return True
        if obj.parent is not None and obj.parent.type == "ARMATURE":
            return True
        if any(bone_group.match(g.name) for g in obj.vertex_groups):
            return True
    return False


class RMV2_OT_lod_overrides_set_defaults(bpy.types.Operator):
    bl_idname = "rmv2.lod_overrides_set_defaults"
    bl_label = "Reset Auto-LOD Overrides to Defaults"
    bl_description = ("Import already sets this up automatically - "
                      "use this to put the 4 default rows back (halving "
                      "decimate ratios; Weighted4 for the first two and "
                      "Weighted2 for the last two if the collection looks "
                      "rigged, Static otherwise) after changing them")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.collection is not None

    def execute(self, context):
        from .export_rmv2 import default_lod_overrides
        rigged = _collection_is_rigged(context.collection)
        default_lod_overrides(context.collection, rigged)
        context.collection.rmv2.active_lod_override_index = 0
        self.report(
            {"INFO"},
            f"Reset to 4 default LOD overrides ({'rigged' if rigged else 'static'})")
        return {"FINISHED"}


class RMV2_OT_copy_settings(bpy.types.Operator):
    bl_idname = "rmv2.copy_settings"
    bl_label = "Copy Total War Settings to Selected"
    bl_description = ("Copy the active object's Total War settings "
                      "(material, "
                      "textures, flags) to all selected mesh objects")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.object is not None
                and len(context.selected_objects) > 1)

    def execute(self, context):
        src = context.object.rmv2
        count = 0
        skipped = set()
        for obj in context.selected_objects:
            if obj == context.object or obj.type != "MESH":
                continue
            dst = obj.rmv2
            for prop in ("vertex_format", "material_id", "material_id_raw",
                         "alpha_mode", "render_flag", "shader_name",
                         "texture_directory", "filters", "matrix_index",
                         "parent_matrix_index", "extra_json"):
                try:
                    setattr(dst, prop, getattr(src, prop))
                except TypeError:
                    # The vertex format list is per container, so copying
                    # an RMV2 mesh's onto a .variant_part_mesh one has no
                    # valid answer. Skip that field rather than the
                    # object - everything else still copies.
                    skipped.add(prop)
            dst.textures.clear()
            for slot in src.textures:
                new = dst.textures.add()
                new.texture_type = slot.texture_type
                new.raw_type = slot.raw_type
                new.path = slot.path
            dst.textures_initialized = src.textures_initialized
            count += 1
        if skipped:
            self.report(
                {"WARNING"},
                "Copied Total War settings to %d object(s); %s not "
                "copied - the target's format cannot store it"
                % (count, ", ".join(sorted(skipped))))
        else:
            self.report({"INFO"},
                        f"Copied Total War settings to {count} object(s)")
        return {"FINISHED"}


class RMV2_OT_setup_lods(bpy.types.Operator):
    bl_idname = "rmv2.setup_lods"
    bl_label = "Setup Total War LOD Collections"
    bl_description = ("Create a Total War model root collection with LOD "
                      "child "
                      "collections and link the selected meshes into every "
                      "LOD (replace per-LOD meshes later as needed)")
    bl_options = {"REGISTER", "UNDO"}

    lod_count: IntProperty(name="LOD Count", default=4, min=1, max=8)

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from .export_rmv2 import default_camera_distance
        meshes = [o for o in context.selected_objects if o.type == "MESH"]
        root = bpy.data.collections.new("rmv2_model")
        context.scene.collection.children.link(root)
        root.rmv2.is_rmv2_root = True
        for i in range(self.lod_count):
            col = bpy.data.collections.new(f"{root.name}_lod{i}")
            root.children.link(col)
            # Linking it under the root is what makes it a LOD.
            col.rmv2.lod_level = i
            col.rmv2.camera_distance = default_camera_distance(
                i, count=self.lod_count)
            col.rmv2.lod_values_set = True
            for obj in meshes:
                col.objects.link(obj)
        # unlink from previous collections so they only live in the lods
        for obj in meshes:
            for col in list(obj.users_collection):
                if not capabilities.is_lod(col):
                    col.objects.unlink(obj)
        self.report({"INFO"},
                    f"Created '{root.name}' with {self.lod_count} LODs")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class RMV2_UL_textures(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "texture_type", text="")
        if item.texture_type == "OTHER":
            row.prop(item, "raw_type", text="")
        row.prop(item, "path", text="")


class RMV2_UL_attach_points(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="EMPTY_AXIS")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.prop(item, "bone_index", text="")


class RMV2_UL_lod_overrides(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.label(text=f"LOD{index}")
        row.prop(item, "vertex_format", text="")
        row.prop(item, "decimate_ratio", text="")
        # `data` is the root collection's settings, so the row knows what
        # it is going to be written as. A .variant_part_mesh part has
        # nowhere to keep a camera distance, and no RMV2 LOD header
        # before v7 has room for a quality level.
        caps = capabilities.collection_caps(data.id_data)
        if "camera_distance" in caps:
            row.prop(item, "camera_distance", text="")
        if "quality_level" in caps:
            row.prop(item, "quality_level", text="")


def _draw_material_names(layout, settings, scope_note: str):
    """A `.variant_part_mesh` material: three names and nothing else.

    No texture slots and no material id - saying that outright beats an
    empty panel that looks like something failed to import.
    """
    box = layout.box()
    box.label(text="Material", icon="MATERIAL")
    box.label(text=scope_note, icon="INFO")
    for entry in settings.material_names:
        box.prop(entry, "name", text="")


def _draw_shader_params(layout, settings, scope_note: str):
    """The named parameter block, wherever it lives.

    Same list in both panels; only the note differs, because where the
    block sits is a fact about the container - see capabilities.py.
    """
    box = layout.box()
    box.label(text=f"Shader Parameters ({len(settings.shader_params)})",
              icon="SHADERFX")
    box.label(text=scope_note, icon="INFO")
    row = box.row()
    row.template_list("RMV2_UL_shader_params", "", settings,
                      "shader_params", settings,
                      "active_shader_param_index", rows=4)
    button_col = row.column(align=True)
    button_col.operator("rmv2.shader_param_add", icon="ADD", text="")
    button_col.operator("rmv2.shader_param_remove", icon="REMOVE", text="")
    index = settings.active_shader_param_index
    if 0 <= index < len(settings.shader_params):
        entry = settings.shader_params[index]
        if entry.kind == "VEC4":
            # The row above is the swatch; these are for typing an exact
            # value, and for the case the swatch cannot show - a
            # component outside 0..1, which the file is allowed to hold.
            grid = box.grid_flow(row_major=True, columns=4, align=True)
            grid.use_property_split = False
            for i, channel in enumerate("RGBA"):
                grid.prop(entry, "vector", index=i, text=channel)


class RMV2_UL_shader_params(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="")
        row.prop(item, "kind", text="")
        if item.kind == "VEC4":
            row.prop(item, "vector", text="")
        else:
            row.prop(item, "value", text="")


class OBJECT_PT_rmv2(bpy.types.Panel):
    bl_label = PANEL_TITLE
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def draw(self, context):
        layout = self.layout
        obj = context.object
        s = obj.rmv2
        if not s.textures_initialized:
            _defer_ensure_default_textures(obj.name)
        layout.use_property_split = True
        layout.use_property_decorate = False

        # Only the fields the model's own format has room for; see
        # capabilities.py for what each container writes.
        caps = capabilities.object_caps(obj)

        col = layout.column()
        col.prop(s, "model_name")
        if "vertex_format" in caps:
            col.prop(s, "vertex_format")
        if "material_id" in caps:
            col.prop(s, "material_id")
            if s.material_id == "OTHER":
                col.prop(s, "material_id_raw")
        if "alpha_mode" in caps:
            col.prop(s, "alpha_mode")
        if "shader_name" in caps:
            col.prop(s, "shader_name")
        if "render_flag" in caps:
            col.prop(s, "render_flag")

        if "textures" in caps:
            box = layout.box()
            box.label(text="Textures", icon="TEXTURE")
            if "texture_directory" in caps:
                box.prop(s, "texture_directory")
            row = box.row()
            row.template_list("RMV2_UL_textures", "", s, "textures", s,
                              "active_texture_index", rows=3)
            button_col = row.column(align=True)
            button_col.operator("rmv2.texture_add", icon="ADD", text="")
            button_col.operator("rmv2.texture_remove", icon="REMOVE",
                                text="")

        if "material_names" in caps:
            # Only a library file (vertex format 2) names materials per
            # part; in the others the part carries no name at all and the
            # three sit once in the file's trailer. Saying which of the
            # two this is beats an empty box either way.
            _draw_material_names(
                layout, s,
                "No texture slots in this format - a part's material is "
                "these three names"
                if s.material_names else
                "No textures in this format, and this file names its "
                "material for the whole model rather than per part")

        if "shader_params" in caps:
            _draw_shader_params(
                layout, s,
                "Set per object in this format - vanilla files do vary "
                "between objects in one file")

        advanced = [name for name in
                    ("matrix_index", "parent_matrix_index", "filters")
                    if name in caps]
        if advanced:
            adv = layout.box()
            adv.label(text="Advanced", icon="PREFERENCES")
            for name in advanced:
                adv.prop(s, name)
            # s.extra_json (raw preserved fields for byte-perfect
            # re-export) intentionally has no UI - it is not meant to be
            # edited.

        layout.operator("rmv2.copy_settings", icon="COPYDOWN")


class COLLECTION_PT_rmv2(bpy.types.Panel):
    bl_label = PANEL_TITLE
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "collection"

    def draw(self, context):
        layout = self.layout
        col = context.collection
        s = col.rmv2
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(s, "is_rmv2_root")
        caps = capabilities.collection_caps(col if s.is_rmv2_root else None)
        if s.is_rmv2_root:
            # One dropdown covering every container this add-on
            # writes, grouped by format: a root collection is one file,
            # and a file has one version.  Each exporter opens on this
            # when it is one of its own versions, and on its own default
            # when the model came from a different format.
            layout.prop(s, "version")
            if "skeleton_name" in caps:
                layout.prop(s, "skeleton_name")

            if "attach_points" in caps:
                box = layout.box()
                box.label(
                    text=f"Attachment Points ({len(s.attach_points)})",
                    icon="EMPTY_AXIS")
                row = box.row()
                row.template_list("RMV2_UL_attach_points", "", s,
                                  "attach_points", s,
                                  "active_attach_index", rows=3)
                button_col = row.column(align=True)
                button_col.operator("rmv2.attach_add", icon="ADD", text="")
                button_col.operator("rmv2.attach_remove", icon="REMOVE",
                                    text="")
                if 0 <= s.active_attach_index < len(s.attach_points):
                    entry = s.attach_points[s.active_attach_index]
                    grid = box.grid_flow(row_major=True, columns=4,
                                         align=True)
                    grid.use_property_split = False
                    for i in range(12):
                        grid.prop(entry, "matrix", index=i, text="")

            if "material_names" in caps and s.material_names:
                # Only version 3 keeps material names here at all: the
                # trailer has four name slots in v3 and one - the
                # skeleton - in v0 and v2. A library file names them per
                # part and leaves this empty too.
                _draw_material_names(
                    layout, s,
                    "Named once for the whole model, in the file's "
                    "trailer")

            if "shader_params" in caps:
                _draw_shader_params(
                    layout, s,
                    "One block for the whole file in this format")

            if "auto_lods" in caps:
                lod_box = layout.box()
                lod_box.label(
                    text=f"Auto-LOD Overrides ({len(s.lod_overrides)})",
                    icon="MOD_DECIM")
                lod_box.label(
                    text="Row count = LODs made by Export > Generate LODs",
                    icon="INFO")
                columns = ["format", "ratio"]
                if "camera_distance" in caps:
                    columns.append("distance")
                if "quality_level" in caps:
                    columns.append("quality")
                lod_box.label(
                    text="Each row overrides that LOD's "
                    + "/".join(columns))
                row = lod_box.row()
                row.template_list("RMV2_UL_lod_overrides", "", s,
                                  "lod_overrides", s,
                                  "active_lod_override_index", rows=3)
                button_col = row.column(align=True)
                button_col.operator("rmv2.lod_override_add", icon="ADD",
                                    text="")
                button_col.operator("rmv2.lod_override_remove",
                                    icon="REMOVE", text="")
                lod_box.operator("rmv2.lod_overrides_set_defaults",
                                 icon="FILE_REFRESH")
            elif "lod_ladder" in caps:
                layout.label(
                    text="LODs are the collections below; this format "
                    "has no decimator", icon="MOD_DECIM")
            else:
                layout.label(
                    text="One file is one LOD in this format",
                    icon="INFO")

        # Being a LOD is not something anyone ticks: a collection is one
        # when it sits inside a model root. So there is one checkbox, and
        # "root and LOD at the same time" is unreachable rather than
        # guarded against.
        parent = capabilities.parent_root_of(col)
        if parent is not None:
            layout.separator()
            box = layout.box()
            box.label(text="LOD of “%s”" % parent.name,
                      icon="OUTLINER_COLLECTION")
            # A LOD's settings come from its root's format, not its own -
            # it has no Version of its own to read.
            lod_caps = capabilities.collection_caps(parent)
            if "lod_levels" in lod_caps:
                box.prop(s, "lod_level")
            else:
                # One file is one LOD in this container and the importer
                # makes exactly one collection, so a level is a number
                # with nothing to vary.
                box.label(text="This format stores one LOD per file",
                          icon="INFO")
            if "camera_distance" in lod_caps:
                box.prop(s, "camera_distance")
            if "quality_level" in lod_caps:
                box.prop(s, "quality_level")


class ARMATURE_PT_rmv2(bpy.types.Panel):
    bl_label = PANEL_TITLE
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return (context.object is not None
                and context.object.type == "ARMATURE")

    def draw(self, context):
        layout = self.layout
        s = context.object.data.rmv2
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(s, "skeleton_name")
        layout.prop(s, "anim_version")
        layout.prop(s, "anim_fps")
        layout.prop(s, "flags")
        layout.prop(s, "anim_header_type")


CLASSES = (
    RMV2_OT_texture_add,
    RMV2_OT_texture_remove,
    RMV2_OT_attach_add,
    RMV2_OT_attach_remove,
    RMV2_OT_shader_param_add,
    RMV2_OT_shader_param_remove,
    RMV2_OT_lod_override_add,
    RMV2_OT_lod_override_remove,
    RMV2_OT_lod_overrides_set_defaults,
    RMV2_OT_copy_settings,
    RMV2_OT_setup_lods,
    RMV2_UL_textures,
    RMV2_UL_attach_points,
    RMV2_UL_lod_overrides,
    RMV2_UL_shader_params,
    OBJECT_PT_rmv2,
    COLLECTION_PT_rmv2,
    ARMATURE_PT_rmv2,
)


def _draw_setup_lods_menu_item(self, context):
    self.layout.operator("rmv2.setup_lods", icon="MOD_DECIM")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # Blender 4.0+'s F3 search only indexes operators that appear in an
    # actual menu, so this must be added to one to be discoverable there
    # (as well as being directly clickable from the Object menu).
    bpy.types.VIEW3D_MT_object.append(_draw_setup_lods_menu_item)


def unregister():
    bpy.types.VIEW3D_MT_object.remove(_draw_setup_lods_menu_item)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
