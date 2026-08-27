"""File > Import / Export operators for .rigid_model_v2 and .anim, the
pre-Rome 2 .animatable_rigid_model / .rigid_model pair, and the unit
formats .variant_part_mesh, .variant_weighted_mesh and
.rigid_model_animation."""

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

from . import (export_anim, export_arm, export_rma, export_rmv2,
               export_vmpf, export_vwm, import_anim, import_arm,
               import_rma, import_rmv2, import_vmpf, import_vwm,
               skeleton)
from .anim_format import AnimFormatError
from .arm_format import ArmFormatError
from .properties import get_texture_root
from .rmv2_format import RmvFormatError
from .vmpf_format import VmpfError
from .vwm_format import VwmFormatError


def _draw_texture_root(layout, operator, context):
    """The Texture Root row, plus what will actually be used.

    The field is a per-import *override* and is empty by default, at
    which point the add-on preference is used instead - which looked
    exactly like nothing was configured at all. So when it is empty, say
    what the preference resolves to, or where to go and set one.
    """
    layout.prop(operator, "texture_root")
    if operator.texture_root:
        return
    preference = get_texture_root(context)
    if preference:
        layout.label(text="Empty: using preference %s" % preference,
                     icon="INFO")
    else:
        layout.label(text="No Texture Root set - no textures will load",
                     icon="ERROR")
        layout.label(text="Set one in Preferences > Add-ons > Total War "
                          "Model")


class IMPORT_SCENE_OT_rmv2(bpy.types.Operator, ImportHelper):
    """Import a Total War RigidModel v2 (.rigid_model_v2) - meshes, LODs
    and materials. Rome 2 to Pharaoh, and Shogun 2's versions 1 - 3.

    With an armature selected the meshes bind to it and their vertex
    groups get real bone names; without one they arrive as bone_<i> and a
    later .anim import renames them retroactively, and welds any piece
    that rides a single bone to it. The skeleton the file asks for is
    kept on the root collection"""
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
        name="Texture Root Override",
        description="Folder with extracted game textures, for this "
        "import only. Leave empty to use the add-on preference "
        "(Preferences > Add-ons > Total War Model), which is where to "
        "set it once for good",
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
            _draw_texture_root(layout, self, context)
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
    """Export a Total War RigidModel v2 (.rigid_model_v2) - meshes, LODs
    and materials. Rome 2 to Pharaoh, and Shogun 2's versions 1 - 3.

    Writes the active model's root collection with its LOD ladder, or
    the selection. The version comes from the collection's own Version
    setting rather than from this dialog, so change it there to write a
    model out for a different game - and a batch export can write each
    collection as its own version"""
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
    """Import a Total War Animation or bind-pose skeleton (.anim) - every
    game.

    Builds a fresh armature if the target model doesn't have one yet and
    the file looks like a bind-pose skeleton (skeleton name 'building', or
    2-3 identical frames like animations/skeletons/*.anim); otherwise keys
    the file onto the model's existing armature as an action"""
    bl_idname = "import_scene.tw_anim"
    bl_label = "Import Animation"
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
    """Export a Total War Animation or bind-pose skeleton (.anim) - every
    game.

    Animation mode samples the selected armature's pose over the scene
    frame range; Bind Pose mode writes its rest pose as a skeleton file,
    two identical frames like the vanilla ones. The version comes from
    the armature (Object Data Properties > Total War Settings) rather
    than from this dialog"""
    bl_idname = "export_scene.tw_anim"
    bl_label = "Export Animation"
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
        layout.prop(self, "skeleton_name")
        if self.mode == "ANIMATION":
            layout.prop(self, "frame_rate")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "mode": self.mode,
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


class IMPORT_SCENE_OT_tw_arm(bpy.types.Operator, ImportHelper):
    """Import a Total War RigidModel (.animatable_rigid_model,
    .rigid_model) - jointed props and static geometry. Empire, Napoleon and
    Shogun 2.

    Both menu rows come here and either opens either form: which one a
    file is gets read out of the file, not its name, so the rows differ
    only in what the browser lists first.

    Each object that rides a bone is welded to it with a Child Of
    constraint - import the .anim before or after, whichever suits: with
    an armature already there it happens now, without one the bone index
    is kept and the .anim import does it. One file is one level of
    detail; the ladder lives in separate _lod1 / _lod2 files"""
    bl_idname = "import_scene.tw_arm"
    bl_label = "Import RigidModel"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".animatable_rigid_model"
    # Blender silently drops filter_glob segments >= 16 chars, so the full
    # "*.animatable_rigid_model" would never match and the browser would
    # look empty. Both short patterns below stay under the limit and
    # between them match the animatable form (which ends in an underscore
    # before "rigid_model") and the plain one.
    # SKIP_SAVE so a bare invocation (F3 search, drag-and-drop) opens on
    # both patterns rather than on whichever menu row was used last - the
    # rows set this explicitly, and an explicit value still wins.
    filter_glob: StringProperty(default="*_rigid_model;*.rigid_model",
                                options={"HIDDEN", "SKIP_SAVE"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH",
                              options={"HIDDEN", "SKIP_SAVE"})

    build_materials: BoolProperty(
        name="Build Materials",
        description="Create Blender materials with image nodes for the "
        "file's textures",
        default=True)
    texture_root: StringProperty(
        name="Texture Root Override",
        description="Folder with extracted game textures, for this "
        "import only. Leave empty to use the add-on preference "
        "(Preferences > Add-ons > Total War Model), which is where to "
        "set it once for good",
        default="", subtype="DIR_PATH")
    attach_armature: BoolProperty(
        name="Attach To Selected Armature",
        description="If an armature is selected (e.g. from importing the "
        "matching .anim), bind each object to the bone its file entry "
        "names, with a Child Of constraint",
        default=True)
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "build_materials")
        if self.build_materials:
            _draw_texture_root(layout, self, context)
        layout.prop(self, "attach_armature")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "build_materials": self.build_materials,
            "texture_root": self.texture_root or get_texture_root(context),
            "attach_armature": self.attach_armature,
            "global_scale": self.global_scale,
        }

        filepaths = []
        if self.files:
            for entry in self.files:
                if entry.name:
                    filepaths.append(os.path.join(self.directory,
                                                  entry.name))
        if not filepaths and self.filepath:
            filepaths = [self.filepath]

        imported = 0
        totals = {"meshes": 0, "vertices": 0, "triangles": 0}
        for path in filepaths:
            try:
                _, stats, warnings = import_arm.import_file(
                    context, path, options)
            except (ArmFormatError, import_arm.ArmImportError) as exc:
                self.report({"ERROR"}, f"{os.path.basename(path)}: {exc}")
                continue
            except Exception as exc:
                traceback.print_exc()
                self.report({"ERROR"},
                            f"{os.path.basename(path)}: unexpected error: "
                            f"{exc}")
                continue
            for warning in warnings:
                self.report({"WARNING"}, warning)
            imported += 1
            for key in totals:
                totals[key] += stats[key]

        if imported == 0:
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Imported {imported} file(s): {totals['meshes']} objects, "
            f"{totals['vertices']} vertices, {totals['triangles']} "
            "triangles")
        return {"FINISHED"}


class _ArmExportBase:
    """The shared half of the two RigidModel export entries.

    `.animatable_rigid_model` and `.rigid_model` are one container: the
    same writer produces both, and what separates them is whether each
    object carries the index of the bone it rides or the file closes with
    a bounding box.  That is a real choice the user makes, so it gets a
    menu row each rather than being hidden behind which extension they
    happened to type.  (Import stays one row: there the two are told
    apart by looking inside the file, so there is nothing to pick.)

    A plain mixin rather than a base Operator - Blender binds one RNA
    struct per registered class, and subclassing a registered Operator
    takes it away from the parent.
    """
    bl_options = {"REGISTER"}

    source: EnumProperty(
        name="Source",
        items=[("AUTO", "Auto",
                "Best LOD of the active model collection if there is one, "
                "otherwise the selection"),
               ("SELECTED", "Selected Objects", ""),
               ("VISIBLE", "Visible Objects", "")],
        default="AUTO")
    apply_modifiers: BoolProperty(
        name="Apply Modifiers", default=True,
        description="Export the evaluated mesh")
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "source")
        layout.prop(self, "apply_modifiers")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "source": self.source,
            "apply_modifiers": self.apply_modifiers,
            "global_scale": self.global_scale,
        }
        try:
            stats, warnings = export_arm.export_file(
                context, self.filepath, options)
        except (export_arm.ArmExportError, ArmFormatError) as exc:
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
            f"Exported {stats['meshes']} objects, {stats['vertices']} "
            f"vertices, {stats['triangles']} triangles "
            f"({stats['bytes']:,} bytes)")
        return {"FINISHED"}


class EXPORT_SCENE_OT_tw_arm(_ArmExportBase, bpy.types.Operator,
                             ExportHelper):
    """Export a Total War Animatable RigidModel (.animatable_rigid_model)
    - jointed props whose parts ride bones, such as siege engines and
    ballistae. Empire, Napoleon and Shogun 2.

    Every object is written with the index of the bone it rides, taken
    from its Child Of constraint. For static geometry use Export >
    RigidModel (.rigid_model) instead, which leaves that out"""
    bl_idname = "export_scene.tw_arm"
    bl_label = "Export Animatable RigidModel"

    filename_ext = ".animatable_rigid_model"
    # See IMPORT_SCENE_OT_tw_arm for why these patterns are abbreviated.
    filter_glob: StringProperty(default="*_rigid_model",
                                options={"HIDDEN"})


class EXPORT_SCENE_OT_tw_rigid(_ArmExportBase, bpy.types.Operator,
                               ExportHelper):
    """Export a Total War RigidModel (.rigid_model) - static geometry.
    Empire, Napoleon and Shogun 2.

    The same container as .animatable_rigid_model minus the per-object
    bone index, closing with a bounding box instead. For props whose
    parts ride bones use Export > Animatable RigidModel"""
    bl_idname = "export_scene.tw_rigid"
    bl_label = "Export RigidModel"

    filename_ext = ".rigid_model"
    filter_glob: StringProperty(default="*.rigid_model",
                                options={"HIDDEN"})


class IMPORT_SCENE_OT_tw_vmpf(bpy.types.Operator, ImportHelper):
    """Import a Total War Variant Part Mesh (.variant_part_mesh) - skinned
    unit parts such as helmets, torsos and saddles. Shogun 2.

    Positions are stored in bone space only, so the part wants its
    reference skeleton: import the .anim the file names and leave that
    armature selected. Without one the bind pose is rebuilt from the
    geometry itself, which is only approximate.

    Rigid parts - equipment, crests, the equipment libraries - are not
    skinned: each rides one named bone and arrives on a Child Of
    constraint instead of vertex groups. Several of those mounts
    (Weapon1..3 on man_shogun) sit at the world origin until an
    animation moves them, so a prop resting at the origin in the T-pose
    is correct"""
    bl_idname = "import_scene.tw_vmpf"
    bl_label = "Import Variant Part Mesh"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".variant_part_mesh"
    # Blender silently drops filter_glob segments >= 16 characters, so the
    # full "*.variant_part_mesh" would never match and the browser would
    # look empty (see IMPORT_SCENE_OT_tw_arm for the same trap).
    filter_glob: StringProperty(default="*_part_mesh", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH",
                              options={"HIDDEN", "SKIP_SAVE"})

    attach_armature: BoolProperty(
        name="Use Selected Armature",
        description="Skin against the selected armature (import the "
        "model's reference .anim first). Without one the bind pose is "
        "rebuilt from the mesh itself, which is only approximate",
        default=True)
    import_lods: EnumProperty(
        name="LODs",
        items=[("ALL", "All", "Import every level of detail"),
               ("FIRST", "Most detailed only",
                "Import only the highest-detail level")],
        default="ALL")
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "attach_armature")
        layout.prop(self, "import_lods")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "attach_armature": self.attach_armature,
            "import_lods": self.import_lods,
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
        skeletons = set()
        totals = {"meshes": 0, "vertices": 0, "triangles": 0}
        for path in filepaths:
            try:
                root, stats, warnings = import_vmpf.import_file(
                    context, path, options)
            except (VmpfError, import_vmpf.VmpfImportError) as exc:
                self.report({"ERROR"}, f"{os.path.basename(path)}: {exc}")
                continue
            except Exception as exc:
                traceback.print_exc()
                self.report({"ERROR"},
                            f"{os.path.basename(path)}: unexpected error: "
                            f"{exc}")
                continue
            for warning in warnings:
                self.report({"WARNING"}, warning)
            imported += 1
            if root.rmv2.skeleton_name:
                skeletons.add(root.rmv2.skeleton_name)
            for key in totals:
                totals[key] += stats[key]

        if imported == 0:
            return {"CANCELLED"}
        # The file names the skeleton it belongs to, so say which one
        # rather than leaving the user to work it out from the folder.
        suffix = (f"; skeleton: {', '.join(sorted(skeletons))}"
                  if skeletons else "")
        self.report(
            {"INFO"},
            f"Imported {imported} file(s): {totals['meshes']} objects, "
            f"{totals['vertices']} vertices, {totals['triangles']} "
            f"triangles{suffix}")
        return {"FINISHED"}


class EXPORT_SCENE_OT_tw_vmpf(bpy.types.Operator, ExportHelper):
    """Export a Total War Variant Part Mesh (.variant_part_mesh) - skinned
    unit parts such as helmets, torsos and saddles. Shogun 2.

    Every vertex is written once per influence, in that bone's own space,
    so the model's armature has to be present to export against: there is
    no model-space position to fall back on.

    This format's parts are its LOD ladder, so Generate LODs builds one
    by decimation off the collection's Auto-LOD Override rows, the same
    as .rigid_model_v2. Not for library files, whose parts are named
    individually in the file"""
    bl_idname = "export_scene.tw_vmpf"
    bl_label = "Export Variant Part Mesh"
    bl_options = {"REGISTER"}

    filename_ext = ".variant_part_mesh"
    filter_glob: StringProperty(default="*_part_mesh", options={"HIDDEN"})

    source: EnumProperty(
        name="Source",
        items=[("AUTO", "Active Model", "The active model's LOD ladder"),
               ("SELECTED", "Selected Objects", "Selected meshes only")],
        default="AUTO")
    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Evaluate modifiers (the armature modifier is always "
        "disabled first, so the rest pose is what gets written)",
        default=True)
    skeleton_name: StringProperty(
        name="Skeleton",
        description="Name of the skeleton this part belongs to, e.g. "
        "man_shogun. Taken from the model's collection when left blank",
        default="")
    auto_lods: BoolProperty(
        name="Generate LODs (Decimate)", default=False,
        description="Off: write the LODs as set up by collections. On: "
        "ignore every LOD collection but the best one and build the "
        "ladder from it by decimation, using the root collection's "
        "Auto-LOD Override rows. This format's parts are its LOD "
        "ladder, the same as .rigid_model_v2's LOD table. Not available "
        "for library files, whose parts are named individually")
    auto_lod_count: IntProperty(
        name="LOD Count", default=4, min=2, max=8,
        description="How many LODs to generate when the root collection "
        "has no override rows")
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "source")
        layout.prop(self, "apply_modifiers")
        layout.prop(self, "skeleton_name")
        layout.prop(self, "auto_lods")
        if self.auto_lods:
            layout.prop(self, "auto_lod_count")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "source": self.source,
            "apply_modifiers": self.apply_modifiers,
            "skeleton_name": self.skeleton_name,
            "global_scale": self.global_scale,
            "auto_lods": self.auto_lods,
            "auto_lod_count": self.auto_lod_count,
        }
        try:
            stats, warnings = export_vmpf.export_file(
                context, self.filepath, options)
        except export_vmpf.VmpfExportError as exc:
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
            f"Exported {stats['lods']} LOD(s), {stats['vertices']} "
            f"vertices, {stats['triangles']} triangles, {stats['bytes']} "
            "bytes")
        return {"FINISHED"}


class IMPORT_SCENE_OT_tw_vwm(bpy.types.Operator, ImportHelper):
    """Import a Total War Variant Weighted Mesh (.variant_weighted_mesh) -
    skinned units, one file per LOD. Empire and Napoleon.

    Positions are stored in bone space only, so import
    animations/reference/tpose.anim first and leave its armature selected -
    every unit in both games is rigged to that one skeleton. Pick all four
    _lodN files at once and they fill in one model's LOD ladder.

    One file holds every alternative a unit's variants can draw, all in
    the same place, so One Variant Per Slot leaves the first of each set
    (head01 of head01..head04) visible and closes the eye on the rest.
    Nothing is deleted and export writes them all"""
    bl_idname = "import_scene.tw_vwm"
    bl_label = "Import Variant Weighted Mesh"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".variant_weighted_mesh"
    # Blender silently drops filter_glob segments >= 16 characters, so the
    # full "*.variant_weighted_mesh" would never match and the browser
    # would look empty (see IMPORT_SCENE_OT_tw_arm for the same trap).
    filter_glob: StringProperty(default="*_weighted_mesh",
                                options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH",
                              options={"HIDDEN", "SKIP_SAVE"})

    build_materials: BoolProperty(
        name="Build Materials",
        description="Create materials from the unit's textures. The file "
        "names none, so they are looked up by convention as "
        "unitmodels/textures/<unit>_diffuse.dds and friends",
        default=True)
    single_variant: BoolProperty(
        name="One Variant Per Slot",
        description="Close the viewport eye on all but the first of each "
        "set of alternatives (head01..head04, body01/body02), so the "
        "model opens as one soldier instead of every variant at once. "
        "The file has no slot field, so parts are grouped by name up to "
        "a trailing number. Nothing is deleted and export still writes "
        "them all",
        default=True)
    texture_root: StringProperty(
        name="Texture Root Override",
        description="Folder with extracted game textures, for this "
        "import only. Leave empty to use the add-on preference "
        "(Preferences > Add-ons > Total War Model), which is where to "
        "set it once for good",
        default="", subtype="DIR_PATH")
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.label(text="Import the reference skeleton first:",
                     icon="INFO")
        layout.label(text=import_vwm.REFERENCE_SKELETON)
        layout.prop(self, "build_materials")
        if self.build_materials:
            _draw_texture_root(layout, self, context)
        layout.prop(self, "single_variant")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "build_materials": self.build_materials,
            "single_variant": self.single_variant,
            "texture_root": self.texture_root or get_texture_root(context),
            "global_scale": self.global_scale,
        }

        filepaths = []
        if self.files:
            for entry in self.files:
                if entry.name:
                    filepaths.append(os.path.join(self.directory, entry.name))
        if not filepaths and self.filepath:
            filepaths = [self.filepath]
        # A unit's LODs are separate files that share one root
        # collection, so import them finest-first and the ladder comes
        # out in order however the browser sorted them.
        filepaths.sort()

        imported = 0
        totals = {"meshes": 0, "vertices": 0, "triangles": 0,
                  "attachments": 0, "hidden": 0}
        for path in filepaths:
            try:
                _, stats, warnings = import_vwm.import_file(
                    context, path, options)
            except (VwmFormatError, import_vwm.VwmImportError) as exc:
                self.report({"ERROR"}, f"{os.path.basename(path)}: {exc}")
                continue
            except Exception as exc:
                traceback.print_exc()
                self.report({"ERROR"},
                            f"{os.path.basename(path)}: unexpected error: "
                            f"{exc}")
                continue
            for warning in warnings:
                self.report({"WARNING"}, warning)
            imported += 1
            for key in totals:
                totals[key] += stats[key]

        if imported == 0:
            return {"CANCELLED"}
        props = (f", {totals['attachments']} of them attached props"
                 if totals["attachments"] else "")
        # Say so rather than leaving the user to wonder where the other
        # half of the part list went.
        hidden = (f"; {totals['hidden']} spare variant(s) hidden"
                  if totals["hidden"] else "")
        self.report(
            {"INFO"},
            f"Imported {imported} file(s): {totals['meshes']} parts"
            f"{props}, {totals['vertices']} vertices, "
            f"{totals['triangles']} triangles{hidden}")
        return {"FINISHED"}


class EXPORT_SCENE_OT_tw_vwm(bpy.types.Operator, ExportHelper):
    """Export a Total War Variant Weighted Mesh (.variant_weighted_mesh) -
    skinned units, one file per LOD. Empire and Napoleon.

    Every vertex is written once per bone that moves it, in that bone's own
    space, so the model's armature has to be present to export against:
    there is no model-space position to fall back on"""
    bl_idname = "export_scene.tw_vwm"
    bl_label = "Export Variant Weighted Mesh"
    bl_options = {"REGISTER"}

    filename_ext = ".variant_weighted_mesh"
    filter_glob: StringProperty(default="*_weighted_mesh",
                                options={"HIDDEN"})

    source: EnumProperty(
        name="Source",
        items=[("AUTO", "Active Model", "The active model's LOD ladder"),
               ("SELECTED", "Selected Objects", "Selected meshes only")],
        default="AUTO")
    all_lods: BoolProperty(
        name="Write All LODs",
        description="Write every LOD collection in the model, one file "
        "each, named <unit>_lod1 to <unit>_lod4 the way CA does. One "
        "file is one LOD in this format, so with this off only the level "
        "below is written and the rest of the model is left behind",
        default=True)
    lod_level: IntProperty(
        name="LOD Level",
        description="Which level to write when Write All LODs is off. "
        "Blender's LOD 0 is CA's <unit>_lod1",
        default=0, min=0, max=7)
    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Evaluate modifiers (the armature modifier is always "
        "disabled first, so the rest pose is what gets written)",
        default=True)
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "source")
        layout.prop(self, "all_lods")
        if not self.all_lods:
            layout.prop(self, "lod_level")
        layout.prop(self, "apply_modifiers")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "source": self.source,
            "lod_level": self.lod_level,
            "apply_modifiers": self.apply_modifiers,
            "global_scale": self.global_scale,
            "auto_lods": False,
        }
        written = []
        try:
            if self.all_lods:
                stats, warnings, written = export_vwm.export_ladder(
                    context, self.filepath, options)
            else:
                stats, warnings = export_vwm.export_file(
                    context, self.filepath, options)
        except (VwmFormatError, export_vwm.VwmExportError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Unexpected error: {exc}")
            return {"CANCELLED"}
        for warning in warnings:
            self.report({"WARNING"}, warning)
        props = (f" ({stats['attachments']} attached props)"
                 if stats["attachments"] else "")
        files = (f"{len(written)} files: "
                 + ", ".join(os.path.basename(p) for p in written) + " - "
                 if len(written) > 1 else "")
        self.report(
            {"INFO"},
            f"Exported {files}{stats['meshes']} parts{props}, "
            f"{stats['vertices']} vertices, {stats['triangles']} "
            f"triangles ({stats['bytes']:,} bytes)")
        return {"FINISHED"}


class IMPORT_SCENE_OT_tw_rma(bpy.types.Operator, ImportHelper):
    """Import a Total War RigidModel Animation (.rigid_model_animation) -
    an object list with its own embedded animation. Empire and Napoleon.

    The skeleton travels inside the file, so there is nothing to import
    first: the armature is built here, the animation arrives as an action
    on it, and each object is welded to the bone it rides"""
    bl_idname = "import_scene.tw_rma"
    bl_label = "Import RigidModel Animation"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".rigid_model_animation"
    # Blender silently drops filter_glob segments >= 16 characters, so
    # the full "*.rigid_model_animation" would never match; this shorter
    # pattern still only matches via the leading wildcard.
    filter_glob: StringProperty(default="*_animation", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH",
                              options={"HIDDEN", "SKIP_SAVE"})

    build_materials: BoolProperty(
        name="Build Materials",
        description="Create Blender materials with image nodes for the "
        "file's textures",
        default=True)
    texture_root: StringProperty(
        name="Texture Root Override",
        description="Folder with extracted game textures, for this "
        "import only. Leave empty to use the add-on preference "
        "(Preferences > Add-ons > Total War Model), which is where to "
        "set it once for good",
        default="", subtype="DIR_PATH")
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "build_materials")
        if self.build_materials:
            _draw_texture_root(layout, self, context)
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "build_materials": self.build_materials,
            "texture_root": self.texture_root or get_texture_root(context),
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
                _, stats, warnings = import_rma.import_file(
                    context, path, options)
            except (ArmFormatError, AnimFormatError,
                    import_rma.RmaImportError) as exc:
                self.report({"ERROR"}, f"{os.path.basename(path)}: {exc}")
                continue
            except Exception as exc:
                traceback.print_exc()
                self.report({"ERROR"},
                            f"{os.path.basename(path)}: unexpected error: "
                            f"{exc}")
                continue
            for warning in warnings:
                self.report({"WARNING"}, warning)
            imported += 1
            for key in totals:
                totals[key] += stats[key]

        if imported == 0:
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Imported {imported} file(s): {totals['meshes']} objects, "
            f"{totals['vertices']} vertices, {totals['triangles']} "
            "triangles")
        return {"FINISHED"}


class EXPORT_SCENE_OT_tw_rma(bpy.types.Operator, ExportHelper):
    """Export a Total War RigidModel Animation (.rigid_model_animation) -
    an object list with its own embedded animation. Empire and Napoleon.

    The objects and a whole headerless .anim go into the one file, so the
    model's armature has to be present to export against: select it, or
    make the model's collection active"""
    bl_idname = "export_scene.tw_rma"
    bl_label = "Export RigidModel Animation"
    bl_options = {"REGISTER"}

    filename_ext = ".rigid_model_animation"
    filter_glob: StringProperty(default="*_animation", options={"HIDDEN"})

    source: EnumProperty(
        name="Source",
        items=[("AUTO", "Active Model", "The active model's objects"),
               ("SELECTED", "Selected Objects", "Selected meshes only")],
        default="AUTO")
    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Evaluate modifiers (the armature modifier is always "
        "disabled first, so the rest pose is what gets written)",
        default=True)
    frame_start: IntProperty(name="Frame Start", default=0)
    frame_end: IntProperty(name="Frame End", default=0)
    global_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, max=1000.0)

    def invoke(self, context, event):
        scene = context.scene
        self.frame_start = scene.frame_start
        self.frame_end = scene.frame_end
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "source")
        layout.prop(self, "apply_modifiers")
        layout.prop(self, "frame_start")
        layout.prop(self, "frame_end")
        layout.prop(self, "global_scale")

    def execute(self, context):
        options = {
            "source": self.source,
            "apply_modifiers": self.apply_modifiers,
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "global_scale": self.global_scale,
            "auto_lods": False,
        }
        try:
            stats, warnings = export_rma.export_file(
                context, self.filepath, options)
        except (ArmFormatError, AnimFormatError,
                export_rma.RmaExportError) as exc:
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
            f"Exported {stats['meshes']} objects, {stats['bones']} bones, "
            f"{stats['frames']} frames ({stats['bytes']:,} bytes)")
        return {"FINISHED"}


# The four formats that only Empire, Napoleon and Shogun 2 use get a
# submenu of their own, so File > Import stays two rows longer rather
# than six. Inside it the rows drop the "Total War" prefix - the parent
# row carries it - and otherwise read exactly as they do at top level.
LEGACY_MENU_LABEL = "Total War, pre-Rome 2"


class TOPBAR_MT_tw_legacy_import(bpy.types.Menu):
    """Import an Empire, Napoleon or Shogun 2 format"""
    bl_idname = "TOPBAR_MT_tw_legacy_import"
    bl_label = LEGACY_MENU_LABEL

    def draw(self, context):
        layout = self.layout
        row = layout.operator(
            IMPORT_SCENE_OT_tw_arm.bl_idname,
            text="Animatable RigidModel (.animatable_rigid_model)")
        row.filter_glob = "*_rigid_model"
        row = layout.operator(IMPORT_SCENE_OT_tw_arm.bl_idname,
                              text="RigidModel (.rigid_model)")
        row.filter_glob = "*.rigid_model"
        layout.operator(IMPORT_SCENE_OT_tw_vmpf.bl_idname,
                        text="Variant Part Mesh (.variant_part_mesh)")
        layout.operator(IMPORT_SCENE_OT_tw_vwm.bl_idname,
                        text="Variant Weighted Mesh "
                             "(.variant_weighted_mesh)")
        layout.operator(IMPORT_SCENE_OT_tw_rma.bl_idname,
                        text="RigidModel Animation "
                             "(.rigid_model_animation)")


class TOPBAR_MT_tw_legacy_export(bpy.types.Menu):
    """Export an Empire, Napoleon or Shogun 2 format"""
    bl_idname = "TOPBAR_MT_tw_legacy_export"
    bl_label = LEGACY_MENU_LABEL

    def draw(self, context):
        layout = self.layout
        layout.operator(EXPORT_SCENE_OT_tw_arm.bl_idname,
                        text="Animatable RigidModel "
                             "(.animatable_rigid_model)")
        layout.operator(EXPORT_SCENE_OT_tw_rigid.bl_idname,
                        text="RigidModel (.rigid_model)")
        layout.operator(EXPORT_SCENE_OT_tw_vmpf.bl_idname,
                        text="Variant Part Mesh (.variant_part_mesh)")
        layout.operator(EXPORT_SCENE_OT_tw_vwm.bl_idname,
                        text="Variant Weighted Mesh "
                             "(.variant_weighted_mesh)")
        layout.operator(EXPORT_SCENE_OT_tw_rma.bl_idname,
                        text="RigidModel Animation "
                             "(.rigid_model_animation)")


def menu_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_rmv2.bl_idname,
                         text="Total War RigidModel v2 (.rigid_model_v2)")
    self.layout.operator(IMPORT_SCENE_OT_tw_anim.bl_idname,
                         text="Total War Animation (.anim)")
    self.layout.menu(TOPBAR_MT_tw_legacy_import.bl_idname)


def menu_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_rmv2.bl_idname,
                         text="Total War RigidModel v2 (.rigid_model_v2)")
    self.layout.operator(EXPORT_SCENE_OT_tw_anim.bl_idname,
                         text="Total War Animation (.anim)")
    self.layout.menu(TOPBAR_MT_tw_legacy_export.bl_idname)


CLASSES = (
    IMPORT_SCENE_OT_rmv2,
    EXPORT_SCENE_OT_rmv2,
    IMPORT_SCENE_OT_tw_arm,
    EXPORT_SCENE_OT_tw_arm,
    EXPORT_SCENE_OT_tw_rigid,
    IMPORT_SCENE_OT_tw_vmpf,
    EXPORT_SCENE_OT_tw_vmpf,
    IMPORT_SCENE_OT_tw_vwm,
    EXPORT_SCENE_OT_tw_vwm,
    IMPORT_SCENE_OT_tw_rma,
    EXPORT_SCENE_OT_tw_rma,
    IMPORT_SCENE_OT_tw_anim,
    EXPORT_SCENE_OT_tw_anim,
    TOPBAR_MT_tw_legacy_import,
    TOPBAR_MT_tw_legacy_export,
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
