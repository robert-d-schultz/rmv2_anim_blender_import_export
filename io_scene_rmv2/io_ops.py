"""File > Import / Export operators for .rigid_model_v2 and .anim."""

from __future__ import annotations

import os
import traceback

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import export_anim, export_rmv2, import_anim, import_rmv2, skeleton
from .anim_format import AnimFormatError
from .properties import VERSION_ITEMS, get_texture_root
from .rmv2_format import RmvFormatError

ANIM_VERSION_ITEMS = [
    ("5", "Anim v5", "Rome 2 era"),
    ("6", "Anim v6", "Attila era"),
    ("7", "Anim v7", "Warhammer 1/2/3 era (the version AssetEditor and "
     "the games' modding pipelines expect)"),
]


class IMPORT_SCENE_OT_rmv2(bpy.types.Operator, ImportHelper):
    """Import a Total War RigidModel (.rigid_model_v2)"""
    bl_idname = "import_scene.rmv2"
    bl_label = "Import RigidModel v2"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".rigid_model_v2"
    # Blender's file browser silently drops any filter_glob segment that's
    # >= 16 chars (BLI_path_extension_check_glob's internal copy buffer),
    # so "*.rigid_model_v2" (16 chars) never matches anything - the filter
    # then hides every file. Dropping the literal dot keeps it at 10 chars.
    filter_glob: StringProperty(default="*_model_v2", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH",
                              options={"HIDDEN", "SKIP_SAVE"})

    import_all_lods: BoolProperty(
        name="Import All LODs",
        description="Import every LOD into its own collection. Off: only "
        "the most detailed LOD",
        default=False)
    build_materials: BoolProperty(
        name="Build Materials",
        description="Create Blender materials with image nodes for the "
        "file's textures",
        default=True)
    texture_root: StringProperty(
        name="Texture Root",
        description="Folder with extracted game textures (overrides the "
        "add-on preference)",
        default="", subtype="DIR_PATH")
    create_attach_empties: BoolProperty(
        name="Attachment Point Empties",
        description="Create empties for the file's attachment points",
        default=False)
    attach_armature: BoolProperty(
        name="Attach To Selected Armature",
        description="If an armature is selected (e.g. from a .anim import),"
        " name vertex groups after its bones and parent the meshes to it",
        default=True)
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "import_all_lods")
        layout.prop(self, "global_scale")
        layout.prop(self, "build_materials")
        if self.build_materials:
            layout.prop(self, "texture_root")
        layout.prop(self, "create_attach_empties")
        layout.prop(self, "attach_armature")

    def execute(self, context):
        options = {
            "import_lods": "ALL" if self.import_all_lods else "FIRST",
            "build_materials": self.build_materials,
            "texture_root": self.texture_root or get_texture_root(context),
            "create_attach_empties": self.create_attach_empties,
            "attach_armature": self.attach_armature,
            "global_scale": self.global_scale,
        }

        filepaths = []
        if self.files:
            for entry in self.files:
                if entry.name:
                    filepaths.append(os.path.join(self.directory, entry.name))
        if not filepaths and self.filepath:
            filepaths = [self.filepath]

        imported = 0
        totals = {"meshes": 0, "vertices": 0, "triangles": 0}
        for path in filepaths:
            try:
                _, stats = import_rmv2.import_file(context, path, options)
            except RmvFormatError as exc:
                self.report({"ERROR"},
                            f"{os.path.basename(path)}: {exc}")
                continue
            except Exception as exc:  # unexpected: show full context
                traceback.print_exc()
                self.report({"ERROR"},
                            f"{os.path.basename(path)}: unexpected error: "
                            f"{exc}")
                continue
            imported += 1
            for key in totals:
                totals[key] += stats[key]

        if imported == 0:
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Imported {imported} file(s): {totals['meshes']} meshes, "
            f"{totals['vertices']} vertices, {totals['triangles']} "
            "triangles")
        return {"FINISHED"}


class EXPORT_SCENE_OT_rmv2(bpy.types.Operator, ExportHelper):
    """Export a Total War RigidModel (.rigid_model_v2)"""
    bl_idname = "export_scene.rmv2"
    bl_label = "Export RigidModel v2"
    bl_options = {"REGISTER"}

    filename_ext = ".rigid_model_v2"
    # See the matching comment in IMPORT_SCENE_OT_rmv2: patterns >= 16 chars
    # are silently dropped by Blender's file browser glob matcher.
    filter_glob: StringProperty(default="*_model_v2", options={"HIDDEN"})

    source: EnumProperty(
        name="Source",
        items=[("AUTO", "Auto",
                "Active RMV2 collection if there is one, otherwise the "
                "selection"),
               ("COLLECTION", "Active Collection",
                "Export the active collection (with LOD sub-collections if "
                "present)"),
               ("SELECTED", "Selected Objects", ""),
               ("VISIBLE", "Visible Objects", ""),
               ("BATCH", "Batch (All RMV2 Collections)",
                "Export every RMV2 root collection in the scene to its own "
                "file, named after the collection, into the chosen "
                "folder")],
        default="AUTO")
    version: EnumProperty(name="Version", items=VERSION_ITEMS, default="7")
    skeleton_name: StringProperty(
        name="Skeleton", default="",
        description="Skeleton name for the file header (e.g. humanoid01). "
        "Defaults to the root collection's RMV2 setting")
    auto_lods: BoolProperty(
        name="Generate LODs (Decimate)", default=False,
        description="Off: export the LODs as set up by collections. On: "
        "ignore any LOD collections except the best (lowest-level) one "
        "and regenerate the rest from it with Decimate modifiers, using "
        "the root collection's Auto-LOD Override rows (Collection > RMV2 "
        "settings) - or 4 LODs with a plain halving ratio if it has none")
    auto_lod_count: IntProperty(
        name="LOD Count", default=4, min=2, max=8,
        description="Fallback LOD count when the root collection has no "
        "Auto-LOD Override rows to read the count from")
    apply_modifiers: BoolProperty(
        name="Apply Modifiers", default=True,
        description="Export the evaluated mesh (armature deform is always "
        "excluded so the rest pose is written)")
    high_precision: BoolProperty(
        name="High Precision Positions", default=True,
        description="Use CA's half-float W-scale trick to squeeze extra "
        "precision out of vertex positions (slower export)")
    write_attach_points: BoolProperty(
        name="Attachment Points", default=True,
        description="Write attachment points (from the collection's stored "
        "list, or generated from the armature's bones)")
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def invoke(self, context, event):
        # Prefill version/skeleton/filename from the active RMV2 root
        # collection. Blender persists operator properties (including
        # filepath) between invocations, so "not self.filepath" never
        # triggers again after the first export - always refresh the
        # filename here (keeping whatever directory was last used) rather
        # than only filling it in when empty.
        root, _ = export_rmv2.gather_lods(context, {"source": "AUTO"})
        if root is not None and root.rmv2.is_rmv2_root:
            self.version = root.rmv2.version
            self.skeleton_name = root.rmv2.skeleton_name
            directory = os.path.dirname(self.filepath) if self.filepath \
                else ""
            self.filepath = os.path.join(
                directory, root.name + self.filename_ext)
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "source")
        if self.source == "BATCH":
            layout.label(
                text="One file per collection, into the chosen folder",
                icon="INFO")
        layout.prop(self, "version")
        if self.source != "BATCH":
            layout.prop(self, "skeleton_name")
        layout.prop(self, "auto_lods")
        layout.prop(self, "global_scale")
        layout.prop(self, "apply_modifiers")
        layout.prop(self, "high_precision")
        layout.prop(self, "write_attach_points")

    def execute(self, context):
        options = {
            "source": self.source,
            "version": self.version,
            "skeleton_name": self.skeleton_name,
            "auto_lods": self.auto_lods,
            "auto_lod_count": self.auto_lod_count,
            "apply_modifiers": self.apply_modifiers,
            "high_precision": self.high_precision,
            "write_attach_points": self.write_attach_points,
            "global_scale": self.global_scale,
        }
        if self.source == "BATCH":
            return self._execute_batch(context, options)

        try:
            stats, warnings = export_rmv2.export_file(
                context, self.filepath, options)
        except (export_rmv2.ExportError, RmvFormatError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Unexpected error: {exc}")
            return {"CANCELLED"}

        for warning in warnings:
            self.report({"WARNING"}, warning)
        self.report(
            {"INFO"},
            f"Exported {stats['lods']} LOD(s), {stats['meshes']} meshes, "
            f"{stats['vertices']} vertices, {stats['triangles']} triangles "
            f"({stats['bytes']:,} bytes)")
        return {"FINISHED"}

    def _execute_batch(self, context, options):
        directory = os.path.dirname(self.filepath)
        try:
            results = export_rmv2.export_batch(context, directory, options)
        except (export_rmv2.ExportError, RmvFormatError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Unexpected error: {exc}")
            return {"CANCELLED"}

        exported = 0
        for _name, stats, warnings in results:
            for warning in warnings:
                self.report({"WARNING"}, warning)
            if stats is not None:
                exported += 1

        if exported == 0:
            self.report({"ERROR"}, "Batch export: nothing was exported")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Batch exported {exported}/{len(results)} RMV2 collection(s) "
            f"to {directory}")
        return {"FINISHED"}


def _run_anim_import(operator, context, options: dict):
    try:
        arm_obj, stats, warnings = import_anim.import_file(
            context, operator.filepath, options)
    except (AnimFormatError, import_anim.AnimImportError) as exc:
        operator.report({"ERROR"},
                        f"{os.path.basename(operator.filepath)}: {exc}")
        return {"CANCELLED"}
    except Exception as exc:
        traceback.print_exc()
        operator.report({"ERROR"},
                        f"{os.path.basename(operator.filepath)}: "
                        f"unexpected error: {exc}")
        return {"CANCELLED"}

    for warning in warnings:
        operator.report({"WARNING"}, warning)
    what = "created armature" if stats["created_armature"] \
        else f"applied to '{arm_obj.name}'"
    operator.report(
        {"INFO"},
        f"Imported '{stats['skeleton_name'] or 'anim'}' ({what}): "
        f"{stats['bones']} bones, {stats['frames']} frames, "
        f"{stats['attached']} meshes attached, "
        f"{stats['renamed_groups']} vertex groups renamed")
    return {"FINISHED"}


class IMPORT_SCENE_OT_tw_anim(bpy.types.Operator, ImportHelper):
    """Import a Total War animation or bind-pose skeleton (.anim).

    Builds a fresh armature if the target model doesn't have one yet and
    the file looks like a bind-pose skeleton (skeleton name 'building', or
    2-3 identical frames like animations/skeletons/*.anim); otherwise keys
    the file onto the model's existing armature as an action"""
    bl_idname = "import_scene.tw_anim"
    bl_label = "Import TW Animation"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".anim"
    filter_glob: StringProperty(default="*.anim", options={"HIDDEN"})

    attach_meshes: BoolProperty(
        name="Attach Meshes",
        description="Rename bone_<i> vertex groups of the target model's "
        "meshes to the armature's bone names, then parent them to it",
        default=True)
    import_animation: BoolProperty(
        name="Import Frames As Action",
        description="When this file is used to build a fresh armature, "
        "also key its frames as an action. Bind-pose skeleton files carry "
        "a few identical frames, so this is only useful if the file "
        "turned out to be a real (if very short) animation. Ignored when "
        "applying to an existing armature - that always keys the frames",
        default=False)
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0,
        description="Must match the scale the meshes were imported with")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "attach_meshes")
        layout.prop(self, "import_animation")
        layout.prop(self, "global_scale")

    def execute(self, context):
        return _run_anim_import(self, context, {
            "attach_meshes": self.attach_meshes,
            "import_animation": self.import_animation,
            "global_scale": self.global_scale,
        })


class EXPORT_SCENE_OT_tw_anim(bpy.types.Operator, ExportHelper):
    """Export a Total War animation or skeleton (.anim)"""
    bl_idname = "export_scene.tw_anim"
    bl_label = "Export TW Animation"
    bl_options = {"REGISTER"}

    filename_ext = ".anim"
    filter_glob: StringProperty(default="*.anim", options={"HIDDEN"})

    mode: EnumProperty(
        name="Mode",
        items=[("ANIMATION", "Animation",
                "Sample the pose over the scene frame range"),
               ("BINDPOSE", "Bind Pose (Skeleton)",
                "Write the armature's rest pose as a skeleton file "
                "(two identical frames, like the vanilla ones)")],
        default="ANIMATION")
    version: EnumProperty(
        name="Version", items=ANIM_VERSION_ITEMS, default="7")
    skeleton_name: StringProperty(
        name="Skeleton", default="",
        description="Skeleton name for the header (e.g. humanoid01). "
        "Defaults to the name stored on the armature at import time")
    frame_rate: FloatProperty(
        name="Frame Rate", default=0.0, min=0.0, max=1000.0,
        description="Frames per second in the header; 0 = use the value "
        "stored on the armature, else the scene frame rate")
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def invoke(self, context, event):
        # Blender persists operator properties (including filepath and
        # skeleton_name) between invocations, so "if not self.X" never
        # triggers again after the first export - always refresh from the
        # context armature here rather than only filling in when empty.
        arm_obj = skeleton.find_context_armature(context)
        if arm_obj is not None:
            stored = arm_obj.data.rmv2.skeleton_name
            name = stored or arm_obj.name
            self.skeleton_name = name
            directory = os.path.dirname(self.filepath) if self.filepath \
                else ""
            self.filepath = os.path.join(directory, name + self.filename_ext)
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "mode")
        layout.prop(self, "version")
        layout.prop(self, "skeleton_name")
        if self.mode == "ANIMATION":
            layout.prop(self, "frame_rate")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "mode": self.mode,
            "version": self.version,
            "skeleton_name": self.skeleton_name,
            "frame_rate": self.frame_rate,
            "global_scale": self.global_scale,
        }
        try:
            stats, warnings = export_anim.export_file(
                context, self.filepath, options)
        except (export_anim.AnimExportError, AnimFormatError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Unexpected error: {exc}")
            return {"CANCELLED"}

        for warning in warnings:
            self.report({"WARNING"}, warning)
        self.report(
            {"INFO"},
            f"Exported '{stats['skeleton_name']}': {stats['bones']} bones, "
            f"{stats['frames']} frames ({stats['bytes']:,} bytes)")
        return {"FINISHED"}


def menu_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_rmv2.bl_idname,
                         text="Total War RigidModel (.rigid_model_v2)")
    self.layout.operator(IMPORT_SCENE_OT_tw_anim.bl_idname,
                         text="Total War Animation (.anim)")


def menu_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_rmv2.bl_idname,
                         text="Total War RigidModel (.rigid_model_v2)")
    self.layout.operator(EXPORT_SCENE_OT_tw_anim.bl_idname,
                         text="Total War Animation (.anim)")


CLASSES = (
    IMPORT_SCENE_OT_rmv2,
    EXPORT_SCENE_OT_rmv2,
    IMPORT_SCENE_OT_tw_anim,
    EXPORT_SCENE_OT_tw_anim,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
