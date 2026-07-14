"""Importer: .anim -> Blender armature / action.

Two explicit modes (two File > Import menu entries):

* SKELETON - build a new armature from the file's bone table. Meant for
  bind-pose skeleton files (animations/skeletons/*.anim), whose single
  frame becomes the rest pose. Refuses to run if the target model
  already has an armature - a model gets exactly one armature, and it
  is always linked into the model's RMV2 root collection.  Any targeted
  meshes are attached to it: `bone_<i>` vertex groups are renamed to the
  file's bone names and an armature modifier + parent is set up.

* ANIMATION - key the file onto the model's existing armature as an
  action, matched by bone name (fallback: by bone index for our
  `bone_<i>` names).  Refuses to run if no armature can be found (the
  skeleton must be imported first).  Bones the file does not animate
  keep the armature's rest pose, which mirrors how the game composes
  animations with the skeleton's bind pose.

"The target model" is resolved from the selection: selected meshes are
expanded to their whole RMV2 model (hidden LOD collections included),
and with nothing selected the active RMV2 collection is used.  The
armature is found via selection, the meshes' modifiers/parents, or the
model's root collection.

Coordinate conversion is the same +90deg-around-X change of basis the mesh
path uses:  t_blender = (x, -z, y),  q_blender = (w, x, -z, y).  Applied
uniformly to every parent-relative transform, it commutes with the
hierarchy, so world positions match the converted meshes.
"""

from __future__ import annotations

import os

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import anim_format as af
from . import skeleton

DEFAULT_BONE_LENGTH = 0.1


class AnimImportError(Exception):
    pass


# ---------------------------------------------------------------------------
# Coordinate conversion (game: Y-up, effectively left-handed -> Blender:
# Z-up right-handed; the map mirrors once - see utils.py)
# ---------------------------------------------------------------------------

def game_to_blender_translation(t) -> Vector:
    return Vector((-float(t[0]), -float(t[2]), float(t[1])))


def game_to_blender_quaternion(q) -> Quaternion:
    """File order is xyzw; Blender wants wxyz. Conjugation by a reflection:
    the axis (vector part) maps through the linear map NEGATED
    (pseudo-vector rule), the angle (w) is unchanged."""
    quat = Quaternion((float(q[3]), float(q[0]), float(q[2]), -float(q[1])))
    if quat.magnitude > 1e-8:
        quat.normalize()
    else:
        quat = Quaternion()
    return quat


def local_matrix(translation, rotation_xyzw, scale: float) -> Matrix:
    """Parent-relative bone matrix in Blender space."""
    mat = game_to_blender_quaternion(rotation_xyzw).to_matrix().to_4x4()
    mat.translation = game_to_blender_translation(translation) * scale
    return mat


# ---------------------------------------------------------------------------
# Target discovery: which model / meshes / armature does the user mean?
# ---------------------------------------------------------------------------

def _rmv2_root_of(collection):
    """The RMV2 root a (LOD) collection belongs to, if any."""
    if collection.rmv2.is_rmv2_root:
        return collection
    if collection.rmv2.is_lod:
        for candidate in bpy.data.collections:
            if candidate.rmv2.is_rmv2_root \
                    and collection.name in {c.name
                                            for c in candidate.children}:
                return candidate
    return None


def _mesh_armature(obj):
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None:
            return mod.object
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    return None


def find_target(context):
    """(root_collection, meshes, armature) for 'the model the user means'.

    Selected meshes are expanded to their whole RMV2 model (the importer
    hides the lower LOD collections, whose meshes can't be selected but
    belong to the model too); with nothing selected, the active RMV2
    collection is the model.  Any of the three results may be None/empty.
    """
    meshes = {}
    roots = {}

    armature = None
    active = context.active_object
    if active is not None and active.type == "ARMATURE":
        armature = active
    if armature is None:
        for obj in context.selected_objects:
            if obj.type == "ARMATURE":
                armature = obj
                break

    for obj in context.selected_objects:
        if obj.type != "MESH":
            continue
        meshes[obj.name] = obj
        for col in obj.users_collection:
            root = _rmv2_root_of(col)
            if root is not None:
                roots[root.name] = root

    if not meshes and not roots:
        active_layer = context.view_layer.active_layer_collection
        if active_layer is not None:
            root = _rmv2_root_of(active_layer.collection)
            if root is not None:
                roots[root.name] = root

    for root in roots.values():
        for obj in root.all_objects:
            if obj.type == "MESH":
                meshes.setdefault(obj.name, obj)

    if armature is None:
        for obj in meshes.values():
            armature = _mesh_armature(obj)
            if armature is not None:
                break
    if armature is None:
        for root in roots.values():
            for obj in root.all_objects:
                if obj.type == "ARMATURE":
                    armature = obj
                    break
            if armature is not None:
                break

    root = next(iter(roots.values()), None)
    return root, list(meshes.values()), armature


# ---------------------------------------------------------------------------
# Armature creation
# ---------------------------------------------------------------------------

def _unique_bone_names(bones) -> list:
    names = []
    seen = set()
    for i, bone in enumerate(bones):
        name = bone.name or skeleton.fallback_bone_name(i)
        if name in seen:
            base = name
            n = 1
            while name in seen:
                name = f"{base}.{n:03d}"
                n += 1
        seen.add(name)
        names.append(name)
    return names


def build_armature(context, anim: af.AnimFile, resolved: af.ResolvedAnim,
                   name: str, scale: float, warnings: list,
                   link_collection=None):
    """New armature object whose rest pose is the file's first frame,
    linked into `link_collection` (default: the active collection)."""
    bones = anim.bones
    bone_names = _unique_bone_names(bones)

    # Armature-space matrix per bone at frame 0 (file order is
    # parent-before-child in CA skeletons, but don't rely on it).
    world = [None] * len(bones)

    def bone_world(i):
        if world[i] is None:
            local = local_matrix(resolved.translations[0][i],
                                 resolved.rotations[0][i], scale)
            parent = bones[i].parent
            if 0 <= parent < len(bones) and parent != i:
                world[i] = bone_world(parent) @ local
            else:
                world[i] = local
        return world[i]

    for i in range(len(bones)):
        bone_world(i)

    missing = [bone_names[i] for i in range(len(bones))
               if not (resolved.has_translation[i]
                       or resolved.has_rotation[i])]
    if missing:
        warnings.append(
            f"{len(missing)} bone(s) have no data in this file (e.g. "
            f"{', '.join(missing[:4])}); their rest pose is a guess. "
            "Import the skeleton .anim instead for a correct bind pose")

    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    (link_collection or context.collection).objects.link(arm_obj)

    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")
    context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edit_bones = arm_data.edit_bones
        created = []
        for i in range(len(bones)):
            eb = edit_bones.new(bone_names[i])
            eb.head = (0.0, 0.0, 0.0)
            eb.tail = (0.0, DEFAULT_BONE_LENGTH * scale, 0.0)
            created.append(eb)
        for i, bone in enumerate(bones):
            if 0 <= bone.parent < len(bones) and bone.parent != i:
                created[i].parent = created[bone.parent]

        # A bone's length is cosmetic; reaching towards the nearest child
        # head makes the skeleton readable in the viewport.
        heads = [m.translation for m in world]
        for i in range(len(bones)):
            children = [j for j, b in enumerate(bones) if b.parent == i]
            distances = [(heads[j] - heads[i]).length for j in children]
            distances = [d for d in distances if d > 1e-5]
            length = min(distances) if distances \
                else DEFAULT_BONE_LENGTH * scale
            created[i].length = max(length, 1e-3)
            created[i].matrix = world[i]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    for i, bone_name in enumerate(bone_names):
        arm_data.bones[bone_name][skeleton.BONE_INDEX_PROP] = i

    arm_data.display_type = "STICK"
    arm_obj.show_in_front = True
    return arm_obj


def _store_metadata(arm_obj, anim: af.AnimFile):
    """Remember the file's header on the armature so exports can default
    to it. Applied animations overwrite what the skeleton import stored -
    the flags/fps belong to the most recent animation."""
    arm_data = arm_obj.data
    if anim.skeleton_name:
        arm_data[skeleton.SKELETON_NAME_PROP] = anim.skeleton_name
    arm_data[skeleton.ANIM_VERSION_PROP] = anim.version
    arm_data[skeleton.ANIM_FPS_PROP] = anim.frame_rate
    if anim.flags:
        arm_data[skeleton.ANIM_FLAGS_PROP] = list(anim.flags)
    elif skeleton.ANIM_FLAGS_PROP in arm_data:
        del arm_data[skeleton.ANIM_FLAGS_PROP]


# ---------------------------------------------------------------------------
# Applying frames as an action
# ---------------------------------------------------------------------------

def _new_action_fcurves(arm_obj, name: str):
    """Create an action, assign it, and return whatever container fcurves
    must be created in.  Handles both legacy and slotted (4.4+) actions."""
    action = bpy.data.actions.new(name)
    anim_data = arm_obj.animation_data_create()
    anim_data.action = action

    if not hasattr(action, "slots"):            # Blender <= 4.3
        return action, action.fcurves

    slot = anim_data.action_slot
    if slot is None:
        slot = action.slots.new(id_type="OBJECT", name=arm_obj.name)
        anim_data.action_slot = slot
    layer = action.layers[0] if len(action.layers) \
        else action.layers.new("Layer")
    strip = layer.strips[0] if len(layer.strips) \
        else layer.strips.new(type="KEYFRAME")
    try:
        channelbag = strip.channelbag(slot, ensure=True)
    except TypeError:
        channelbag = strip.channelbag(slot, ensure_created=True)
    return action, channelbag.fcurves


def _fill_fcurves(fcurves, data_path: str, count: int, values,
                  frames, group_name: str):
    """One fcurve per component; values is (num_frames, count)."""
    num = len(frames)
    for component in range(count):
        try:
            fcu = fcurves.new(data_path, index=component,
                              action_group=group_name)
        except TypeError:
            fcu = fcurves.new(data_path, index=component)
        fcu.keyframe_points.add(num)
        flat = [0.0] * (num * 2)
        for k in range(num):
            flat[k * 2] = float(frames[k])
            flat[k * 2 + 1] = float(values[k][component])
        fcu.keyframe_points.foreach_set("co", flat)
        for kp in fcu.keyframe_points:
            kp.interpolation = "LINEAR"
        fcu.update()


def apply_animation(context, arm_obj, anim: af.AnimFile,
                    resolved: af.ResolvedAnim, action_name: str,
                    scale: float, warnings: list) -> int:
    """Key the resolved frames onto arm_obj's pose bones. Returns the
    number of bones that received keys."""
    bones = anim.bones
    num_frames = len(resolved.translations)

    index_to_pose = {}
    name_by_index = {i: (b.name or skeleton.fallback_bone_name(i))
                     for i, b in enumerate(bones)}
    stamped = {index: bone.name
               for index, bone in skeleton.bone_index_pairs(arm_obj)}
    unmatched = []
    for i in range(len(bones)):
        pb = arm_obj.pose.bones.get(name_by_index[i])
        if pb is None and i in stamped:
            pb = arm_obj.pose.bones.get(stamped[i])
        if pb is None:
            pb = arm_obj.pose.bones.get(skeleton.fallback_bone_name(i))
        if pb is None:
            unmatched.append(name_by_index[i])
        else:
            index_to_pose[i] = pb
    if unmatched:
        warnings.append(
            f"{len(unmatched)} bone(s) in the file have no match on "
            f"armature '{arm_obj.name}' (e.g. {', '.join(unmatched[:4])})")
    if not index_to_pose:
        raise AnimImportError(
            f"No bone of armature '{arm_obj.name}' matches the file "
            f"(skeleton '{anim.skeleton_name}'). Wrong armature selected?")

    # Parent-relative rest matrix per pose bone, to turn the file's
    # parent-relative animation into pose-space (matrix_basis) values.
    action, fcurves = _new_action_fcurves(arm_obj, action_name)
    keyed = 0
    for i, pb in index_to_pose.items():
        has_t = resolved.has_translation[i]
        has_r = resolved.has_rotation[i]
        if not has_t and not has_r:
            continue

        bone = pb.bone
        if bone.parent is not None:
            rest = bone.parent.matrix_local.inverted() @ bone.matrix_local
        else:
            rest = bone.matrix_local.copy()
        rest_inv = rest.inverted()
        rest_quat = rest.to_quaternion()
        rest_loc = rest.translation

        pb.rotation_mode = "QUATERNION"

        locations = []
        quaternions = []
        prev_quat = None
        for f in range(num_frames):
            if has_t:
                loc = game_to_blender_translation(
                    resolved.translations[f][i]) * scale
            else:
                loc = rest_loc
            if has_r:
                rot = game_to_blender_quaternion(resolved.rotations[f][i])
            else:
                rot = rest_quat
            basis = rest_inv @ Matrix.LocRotScale(loc, rot, None)
            locations.append(basis.to_translation()[:])
            quat = basis.to_quaternion()
            if prev_quat is not None and prev_quat.dot(quat) < 0.0:
                quat.negate()
            prev_quat = quat
            quaternions.append((quat.w, quat.x, quat.y, quat.z))

        # Constant tracks (static frame / single-frame files) only need
        # one key.
        frames = list(range(num_frames))
        if has_t:
            t_frames, t_values = frames, locations
            if resolved.static_translation[i] or num_frames == 1:
                t_frames, t_values = [0], locations[:1]
            _fill_fcurves(fcurves, f'pose.bones["{pb.name}"].location', 3,
                          t_values, t_frames, pb.name)
        if has_r:
            r_frames, r_values = frames, quaternions
            if resolved.static_rotation[i] or num_frames == 1:
                r_frames, r_values = [0], quaternions[:1]
            _fill_fcurves(fcurves,
                          f'pose.bones["{pb.name}"].rotation_quaternion', 4,
                          r_values, r_frames, pb.name)
        keyed += 1

    scene = context.scene
    if anim.frame_rate > 0:
        scene.render.fps = max(1, round(anim.frame_rate))
        scene.render.fps_base = scene.render.fps / anim.frame_rate
    scene.frame_start = 0
    scene.frame_end = max(0, num_frames - 1)
    scene.frame_set(0)
    return keyed


# ---------------------------------------------------------------------------
# Attaching meshes
# ---------------------------------------------------------------------------

def rename_groups_and_attach(meshes, arm_obj, warnings) -> tuple:
    """Rename bone_<i> vertex groups to the armature's bone names and
    attach each mesh. Returns (attached, renamed)."""
    name_by_index = skeleton.bone_name_by_index(arm_obj)
    bone_names = {b.name for b in arm_obj.data.bones}
    attached = 0
    renamed = 0
    for obj in meshes:
        for vgroup in obj.vertex_groups:
            if vgroup.name in bone_names:
                continue
            index = _parse_fallback_name(vgroup.name)
            if index is None:
                continue
            target = name_by_index.get(index)
            if target is None:
                warnings.append(
                    f"{obj.name}: vertex group '{vgroup.name}' has no "
                    f"bone {index} on armature '{arm_obj.name}'")
            elif target in {g.name for g in obj.vertex_groups}:
                warnings.append(
                    f"{obj.name}: cannot rename '{vgroup.name}' - a group "
                    f"named '{target}' already exists")
            else:
                vgroup.name = target
                renamed += 1
        skeleton.attach_mesh(obj, arm_obj)
        attached += 1
    return attached, renamed


def _parse_fallback_name(name: str):
    if not name.startswith("bone_"):
        return None
    suffix = name[5:]
    return int(suffix) if suffix.isdigit() else None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def import_file(context, filepath: str, options: dict):
    """Import one .anim file. options["mode"] is "SKELETON" or
    "ANIMATION" (see module docstring).
    Returns (armature_object, stats, warnings)."""
    warnings: list[str] = []
    with open(filepath, "rb") as handle:
        data = handle.read()
    anim = af.load(data)

    if len(anim.parts) > 1:
        warnings.append(
            f"File has {len(anim.parts)} animation parts; only the first "
            "is imported")
    resolved = af.resolve(anim, 0)
    num_frames = len(resolved.translations)
    scale = options.get("global_scale", 1.0)
    stem = os.path.splitext(os.path.basename(filepath))[0]
    mode = options.get("mode", "ANIMATION")

    root_col, meshes, arm_obj = find_target(context)

    # Skeleton and animation files are structurally identical (bind-pose
    # skeletons are just short animations whose 2-3 frames all repeat the
    # bind pose), so no attempt is made to tell them apart - the mode is
    # the user's statement of intent.
    created = False
    if mode == "SKELETON":
        if arm_obj is not None:
            target = root_col.name if root_col is not None \
                else arm_obj.name
            raise AnimImportError(
                f"'{target}' already has an armature "
                f"('{arm_obj.name}') - a model gets exactly one. Use "
                "File > Import > Total War Animation (.anim) to apply "
                "animations to it")
        arm_name = anim.skeleton_name or stem
        arm_obj = build_armature(context, anim, resolved, arm_name, scale,
                                 warnings, link_collection=root_col)
        created = True
    elif arm_obj is None:
        raise AnimImportError(
            "No armature found in the selection / active collection. "
            "Import the model's skeleton first: File > Import > "
            "Total War Skeleton (.anim)")

    _store_metadata(arm_obj, anim)

    keyed = 0
    if not created or (num_frames > 1 and options.get("import_animation",
                                                      False)):
        keyed = apply_animation(context, arm_obj, anim, resolved, stem,
                                scale, warnings)

    attached = renamed = 0
    if options.get("attach_meshes", True):
        attached, renamed = rename_groups_and_attach(meshes, arm_obj,
                                                     warnings)

    try:
        if bpy.ops.object.select_all.poll():
            bpy.ops.object.select_all(action="DESELECT")
        arm_obj.select_set(True)
        context.view_layer.objects.active = arm_obj
    except RuntimeError:
        pass    # armature in a hidden collection; selection is cosmetic

    stats = {
        "bones": anim.bone_count,
        "frames": num_frames,
        "keyed_bones": keyed,
        "attached": attached,
        "renamed_groups": renamed,
        "created_armature": created,
        "skeleton_name": anim.skeleton_name,
    }
    return arm_obj, stats, warnings
