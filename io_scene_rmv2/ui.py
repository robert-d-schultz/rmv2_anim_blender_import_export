"""UI: RMV2 panels in the Object and Collection property tabs, the texture
slot list, and small workflow operators."""

from __future__ import annotations

import bpy
from bpy.props import IntProperty

# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


class RMV2_OT_texture_add(bpy.types.Operator):
    bl_idname = "rmv2.texture_add"
    bl_label = "Add Texture Slot"
    bl_description = "Add an RMV2 texture slot to the active object"
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
    bl_description = "Remove the selected RMV2 texture slot"
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


class RMV2_OT_copy_settings(bpy.types.Operator):
    bl_idname = "rmv2.copy_settings"
    bl_label = "Copy RMV2 Settings to Selected"
    bl_description = ("Copy the active object's RMV2 settings (material, "
                      "textures, flags) to all selected mesh objects")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.object is not None
                and len(context.selected_objects) > 1)

    def execute(self, context):
        src = context.object.rmv2
        count = 0
        for obj in context.selected_objects:
            if obj == context.object or obj.type != "MESH":
                continue
            dst = obj.rmv2
            for prop in ("vertex_format", "material_id", "material_id_raw",
                         "alpha_mode", "render_flag", "shader_name",
                         "texture_directory", "filters", "matrix_index",
                         "parent_matrix_index", "extra_json"):
                setattr(dst, prop, getattr(src, prop))
            dst.textures.clear()
            for slot in src.textures:
                new = dst.textures.add()
                new.texture_type = slot.texture_type
                new.raw_type = slot.raw_type
                new.path = slot.path
            count += 1
        self.report({"INFO"}, f"Copied RMV2 settings to {count} object(s)")
        return {"FINISHED"}


class RMV2_OT_setup_lods(bpy.types.Operator):
    bl_idname = "rmv2.setup_lods"
    bl_label = "Setup RMV2 LOD Collections"
    bl_description = ("Create an RMV2 root collection with LOD child "
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
            col.rmv2.is_lod = True
            col.rmv2.lod_level = i
            col.rmv2.camera_distance = default_camera_distance(i)
            for obj in meshes:
                col.objects.link(obj)
        # unlink from previous collections so they only live in the lods
        for obj in meshes:
            for col in list(obj.users_collection):
                if not col.rmv2.is_lod:
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


class OBJECT_PT_rmv2(bpy.types.Panel):
    bl_label = "RMV2 (RigidModel)"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def draw(self, context):
        layout = self.layout
        s = context.object.rmv2
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column()
        col.prop(s, "model_name")
        col.prop(s, "vertex_format")
        col.prop(s, "material_id")
        if s.material_id == "OTHER":
            col.prop(s, "material_id_raw")
        col.prop(s, "alpha_mode")
        col.prop(s, "shader_name")
        col.prop(s, "render_flag")

        box = layout.box()
        box.label(text="Textures", icon="TEXTURE")
        box.prop(s, "texture_directory")
        row = box.row()
        row.template_list("RMV2_UL_textures", "", s, "textures", s,
                          "active_texture_index", rows=3)
        button_col = row.column(align=True)
        button_col.operator("rmv2.texture_add", icon="ADD", text="")
        button_col.operator("rmv2.texture_remove", icon="REMOVE", text="")

        adv = layout.box()
        adv.label(text="Advanced", icon="PREFERENCES")
        adv.prop(s, "matrix_index")
        adv.prop(s, "parent_matrix_index")
        adv.prop(s, "filters")
        # s.extra_json (raw preserved fields for byte-perfect re-export)
        # intentionally has no UI - it's not meant to be edited.

        layout.operator("rmv2.copy_settings", icon="COPYDOWN")


class COLLECTION_PT_rmv2(bpy.types.Panel):
    bl_label = "RMV2 (RigidModel)"
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
        if s.is_rmv2_root:
            layout.prop(s, "version")
            layout.prop(s, "skeleton_name")

            box = layout.box()
            box.label(text=f"Attachment Points ({len(s.attach_points)})",
                      icon="EMPTY_AXIS")
            row = box.row()
            row.template_list("RMV2_UL_attach_points", "", s,
                              "attach_points", s, "active_attach_index",
                              rows=3)
            button_col = row.column(align=True)
            button_col.operator("rmv2.attach_add", icon="ADD", text="")
            button_col.operator("rmv2.attach_remove", icon="REMOVE",
                                text="")
            if 0 <= s.active_attach_index < len(s.attach_points):
                entry = s.attach_points[s.active_attach_index]
                grid = box.grid_flow(row_major=True, columns=4, align=True)
                grid.use_property_split = False
                for i in range(12):
                    grid.prop(entry, "matrix", index=i, text="")

        layout.separator()
        layout.prop(s, "is_lod")
        if s.is_lod:
            layout.prop(s, "lod_level")
            layout.prop(s, "camera_distance")
            layout.prop(s, "quality_level")


CLASSES = (
    RMV2_OT_texture_add,
    RMV2_OT_texture_remove,
    RMV2_OT_attach_add,
    RMV2_OT_attach_remove,
    RMV2_OT_copy_settings,
    RMV2_OT_setup_lods,
    RMV2_UL_textures,
    RMV2_UL_attach_points,
    OBJECT_PT_rmv2,
    COLLECTION_PT_rmv2,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
