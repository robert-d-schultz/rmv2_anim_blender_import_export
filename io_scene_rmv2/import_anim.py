"""Importer: .anim -> Blender armature / action.

One File > Import menu entry; the intent (build a new armature, or apply
the file onto an existing one) is inferred per-file - see
`import_file`/`_looks_like_bindpose` - rather than chosen by the user:

* Building a fresh armature: from the file's bone table, its first frame
  becomes the rest pose. Refuses to run if the target model already has
  an armature - a model gets exactly one armature, and it is always
  linked into the model's RMV2 root collection.  Any targeted meshes are
  attached to it: `bone_<i>` vertex groups are renamed to the file's bone
  names and an armature modifier + parent is set up.

* Applying as an animation: key the file onto the model's existing
  armature as an action, matched by bone name (fallback: by bone index
  for our `bone_<i>` names).  Refuses to run if no armature can be found
  and the file doesn't look like a skeleton either, or if the armature's
  stored skeleton name doesn't match the file's (wrong armature selected).
  Bones the file does not animate keep the armature's rest pose, which
  mirrors how the game composes animations with the skeleton's bind pose.

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
import numpy as np
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


def _existing_armature_for_target(root_col, meshes):
    """Armature genuinely known to belong to the resolved target model (via
    its meshes' modifiers/parents, or linked into its root collection) -
    unlike find_target's `armature` result, this deliberately ignores
    whatever happens to be the bare active/selected object, which isn't
    reliable evidence that THIS SPECIFIC model/target already has one.

    Used only for the SKELETON path's "already has an armature" guard: a
    skeleton imported with no model/root context yet (nothing to conflict
    with) must be allowed to build a second, independent armature, but a
    root collection that already contains one must still refuse."""
    for obj in meshes:
        armature = _mesh_armature(obj)
        if armature is not None:
            return armature
    if root_col is not None:
        for obj in root_col.all_objects:
            if obj.type == "ARMATURE":
                return obj
    return None


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

        # A bone's length/direction is cosmetic; the file's rotation data
        # doesn't generally point a bone's local Y axis (the direction
        # Blender draws the stick in) at any particular child - CA's rigs
        # aren't authored with that constraint - so we apply a per-bone
        # cosmetic rotation `fix[i]` that re-aims Y at whichever child
        # best represents "the direction this bone visually continues
        # in": the child that owns a clear majority (>50%) of the bone's
        # total descendant count (so a spine bone with one dominant
        # continuation and a small side-branch, e.g. a clavicle, still
        # lines up with the spine), or the midpoint of all immediate
        # children when no child has a majority (e.g. a symmetric
        # two-legged pelvis split, or two tied leaf children). A leaf
        # bone (no children), OR a bone whose children happen to
        # straddle it symmetrically so their midpoint sits right on top
        # of it (e.g. a vehicle axle centered exactly between its two
        # wheels - real case, steamtank01.anim's axel_front_0/wheel_left
        # _0/wheel_right_0 - the offset-to-aim-at would be ~zero-length,
        # not a meaningful direction), continues its parent's incoming
        # direction instead; a true leaf additionally scales its length
        # to 55% of the parent's. The alternative, leaving the raw file
        # rotation, looks just as arbitrary as an unfixed bone would (and
        # was the original ~90-degrees-off bug this mechanism exists to
        # fix - the axle case above regressed straight back into it
        # before this fallback existed). This only touches the *visual*
        # rest orientation: since it's a constant local rotation, it
        # cancels out of the deform matrix (posed @ rest.inverted())
        # entirely, so animation and skinning are unaffected as long as
        # apply_animation/export undo the same fix - see
        # skeleton.bone_fix_quaternion.
        children_of = [[] for _ in bones]
        for i, bone in enumerate(bones):
            if 0 <= bone.parent < len(bones) and bone.parent != i:
                children_of[bone.parent].append(i)

        subtree_sizes = [None] * len(bones)

        def subtree_size(i):
            if subtree_sizes[i] is None:
                subtree_sizes[i] = 1 + sum(subtree_size(c)
                                           for c in children_of[i])
            return subtree_sizes[i]

        heads = [m.translation for m in world]
        fixes = [Quaternion()] * len(bones)
        lengths = [None] * len(bones)

        def compute(i):
            if lengths[i] is not None:
                return
            kids = children_of[i]
            offset = None
            length = DEFAULT_BONE_LENGTH * scale
            if kids:
                total_descendants = subtree_size(i) - 1
                majority = max(kids, key=subtree_size)
                if subtree_size(majority) > total_descendants / 2:
                    offset = heads[majority] - heads[i]
                else:
                    midpoint = sum((heads[c] for c in kids),
                                  Vector()) / len(kids)
                    offset = midpoint - heads[i]
                if offset.length > 1e-5:
                    length = offset.length
                else:
                    # Children can straddle this bone symmetrically (e.g.
                    # an axle centered exactly between two wheels), which
                    # leaves no meaningful "aim at the children" direction
                    # - fall through to the parent-direction fallback
                    # below instead of leaving fixes[i] at identity, which
                    # would just show the raw, often-wrong file rotation
                    # (the original bug this mechanism exists to fix).
                    offset = None
            if offset is None:
                parent = bones[i].parent
                if 0 <= parent < len(bones) and parent != i:
                    compute(parent)
                    if not kids:    # true leaf: also scale the length
                        length = lengths[parent] * 0.55
                    incoming = heads[i] - heads[parent]
                    if incoming.length > 1e-5:
                        offset = incoming
            if offset is not None and offset.length > 1e-5:
                d_local = world[i].to_3x3().inverted() @ offset.normalized()
                fixes[i] = Vector((0.0, 1.0, 0.0)).rotation_difference(
                    d_local)
            lengths[i] = max(length, 1e-3)

        for i in range(len(bones)):
            compute(i)
        for i in range(len(bones)):
            created[i].length = lengths[i]
            created[i].matrix = world[i] @ fixes[i].to_matrix().to_4x4()
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    for i, bone_name in enumerate(bone_names):
        arm_data.bones[bone_name][skeleton.BONE_INDEX_PROP] = i
        if fixes[i].angle > 1e-6:
            arm_data.bones[bone_name][skeleton.BONE_FIX_QUAT_PROP] = \
                fixes[i][:]

    arm_data.display_type = "STICK"
    arm_obj.show_in_front = True
    return arm_obj


def _store_metadata(arm_obj, anim: af.AnimFile):
    """Remember the file's header on the armature (RMV2 (.anim) panel,
    Object Data Properties) so exports can default to it. Applied
    animations overwrite what the skeleton import stored - the
    flags/fps belong to the most recent animation."""
    s = arm_obj.data.rmv2
    if anim.skeleton_name:
        s.skeleton_name = anim.skeleton_name
    s.anim_version = anim.version
    s.anim_fps = anim.frame_rate
    s.flags = ", ".join(anim.flags)


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

        # Undo the cosmetic per-bone rest-orientation fix (see
        # build_armature) before combining with `rest`, which already has
        # it baked in: fix_p cancels when both bone and parent carry the
        # same identity default, so this is a no-op for old/hand-made
        # armatures.
        fix_c = skeleton.bone_fix_quaternion(bone).to_matrix().to_4x4()
        fix_p_inv = (skeleton.bone_fix_quaternion(bone.parent).to_matrix()
                    .to_4x4().inverted() if bone.parent is not None
                    else Matrix.Identity(4))

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
            local = Matrix.LocRotScale(loc, rot, None)
            corrected = fix_p_inv @ local @ fix_c
            basis = rest_inv @ corrected
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

def _looks_like_bindpose(resolved: af.ResolvedAnim) -> bool:
    """Real bind-pose skeleton files carry 2-3 identical frames - they are
    structurally indistinguishable from a very short static animation, so
    this is a heuristic, not a format marker."""
    num_frames = len(resolved.translations)
    if not 1 <= num_frames <= 3:
        return False
    return (np.allclose(resolved.translations, resolved.translations[:1])
            and np.allclose(resolved.rotations, resolved.rotations[:1]))


def import_file(context, filepath: str, options: dict):
    """Import one .anim file. Builds a fresh armature or applies the file
    onto an existing one as an action; options["mode"] can force
    "SKELETON"/"ANIMATION" (used by tests and other programmatic callers).
    Otherwise the intent is inferred from the file alone (not from whether
    some armature happens to be selected/active - see _looks_like_bindpose
    and the "building" check below), matching the pre-unification
    Skeleton/Animation menu split:
    - the skeleton name is "building" (ad-hoc animations for buildings/
      props never ship a separate bind-pose skeleton file) or the file
      looks like a bind pose -> build a fresh armature. Refuses if the
      resolved target model (its meshes or root collection - see
      _existing_armature_for_target) already has one, since a model gets
      exactly one; a skeleton imported with no model/root context yet
      (nothing to conflict with) is always allowed to build a second,
      independent armature, even if some other armature happens to still
      be the active object.
    - otherwise -> apply as an animation onto the target armature, erroring
      if none is found, or if the armature's stored skeleton name doesn't
      match this file's (wrong armature selected).
    Returns (armature_object, stats, warnings)."""
    warnings: list[str] = []
    with open(filepath, "rb") as handle:
        data = handle.read()
    anim = af.load(data)

    resolved = af.resolve_all(anim)
    num_frames = len(resolved.translations)
    scale = options.get("global_scale", 1.0)
    stem = os.path.splitext(os.path.basename(filepath))[0]
    is_building = anim.skeleton_name.strip().lower() == "building"

    root_col, meshes, arm_obj = find_target(context)

    mode = options.get("mode")
    if mode is None:
        mode = "SKELETON" if (is_building or _looks_like_bindpose(resolved)) \
            else "ANIMATION"

    created = False
    if mode == "SKELETON":
        existing = _existing_armature_for_target(root_col, meshes)
        if existing is not None:
            target = root_col.name if root_col is not None \
                else existing.name
            raise AnimImportError(
                f"'{target}' already has an armature "
                f"('{existing.name}') - a model gets exactly one. To "
                "import a second, independent copy of this skeleton for a "
                "different model, select that model's mesh/collection "
                "first. To apply this file as an animation, pass "
                "mode=ANIMATION")
        # Ad-hoc "building" files never ship a separate bind-pose skeleton
        # file, so the armature is named after the .anim file itself; the
        # skeleton name ("building", shared by every ad-hoc file) still
        # goes into the stored metadata below, not the object name.
        arm_name = stem if is_building else (anim.skeleton_name or stem)
        arm_obj = build_armature(context, anim, resolved, arm_name, scale,
                                 warnings, link_collection=root_col)
        created = True
    elif arm_obj is None:
        raise AnimImportError(
            "No armature found in the selection / active collection, and "
            "this file doesn't look like a bind-pose skeleton (a skeleton "
            "name of 'building', or 2-3 identical frames). Import the "
            "model's skeleton first.")
    else:
        stored_name = arm_obj.data.rmv2.skeleton_name
        if stored_name and anim.skeleton_name and \
                stored_name.strip().lower() != anim.skeleton_name.strip() \
                .lower():
            raise AnimImportError(
                f"'{arm_obj.name}' is skeleton '{stored_name}', but this "
                f"file is for skeleton '{anim.skeleton_name}' - wrong "
                "armature selected?")

    _store_metadata(arm_obj, anim)

    # Ad-hoc "building" animations have no separate bind-pose file, so the
    # frames built above ARE the animation - key them immediately instead
    # of requiring a second, identical import once the armature exists.
    keyed = 0
    if not created or is_building or (num_frames > 1
                                      and options.get("import_animation",
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
