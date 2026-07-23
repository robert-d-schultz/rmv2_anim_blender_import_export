"""Armature helpers shared by the RMV2 and .anim import/export paths.

Game files address bones by index, Blender addresses them by name.  When
this add-on creates an armature it stamps every bone with a
`rmv2_bone_index` custom property so the original file order survives
whatever order Blender keeps its bone collection in.  Armatures made by
hand (or by other add-ons) fall back to Blender's bone order.
"""

from __future__ import annotations

from mathutils import Matrix, Quaternion

BONE_INDEX_PROP = "rmv2_bone_index"
BONE_FIX_QUAT_PROP = "rmv2_bone_fix_quat"


def fallback_bone_name(index: int) -> str:
    """The standard name for a bone whose real name is unknown; the .anim
    importer retroactively renames vertex groups that follow it."""
    return f"bone_{index}"


def bone_index_pairs(armature_obj) -> list:
    """[(file_bone_index, Bone)] sorted by index.

    Uses the `rmv2_bone_index` custom property where present, Blender's
    bone order for the rest (foreign armatures).
    """
    pairs = []
    taken = set()
    unindexed = []
    for i, bone in enumerate(armature_obj.data.bones):
        stored = bone.get(BONE_INDEX_PROP)
        if stored is not None and int(stored) >= 0:
            pairs.append((int(stored), bone))
            taken.add(int(stored))
        else:
            unindexed.append((i, bone))
    # Bones without the property keep their collection position unless a
    # stamped bone already owns it.
    next_free = 0
    for position, bone in unindexed:
        index = position
        if index in taken:
            index = next_free
            while index in taken:
                index += 1
        taken.add(index)
        next_free = index + 1
        pairs.append((index, bone))
    pairs.sort(key=lambda pair: pair[0])
    return pairs


def bone_name_by_index(armature_obj) -> dict:
    """{file_bone_index: bone_name}."""
    return {index: bone.name for index, bone in bone_index_pairs(armature_obj)}


def bone_index_by_name(armature_obj) -> dict:
    """{bone_name: file_bone_index}."""
    return {bone.name: index for index, bone in bone_index_pairs(armature_obj)}


def bone_fix_quaternion(bone) -> Quaternion:
    """The cosmetic rest-orientation correction stamped on `bone` at import
    time (see import_anim.build_armature), identity if absent - bones from
    old files or hand-made armatures are unaffected."""
    raw = bone.get(BONE_FIX_QUAT_PROP)
    if raw is None:
        return Quaternion()
    return Quaternion(tuple(raw))


def find_context_armature(context):
    """Armature the user most plausibly means: the active object if it is
    one, otherwise the first selected armature, otherwise the armature a
    selected mesh is attached to."""
    obj = context.active_object
    if obj is not None and obj.type == "ARMATURE":
        return obj
    for obj in context.selected_objects:
        if obj.type == "ARMATURE":
            return obj
    for obj in context.selected_objects:
        if obj.type != "MESH":
            continue
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.object is not None:
                return mod.object
        if obj.parent is not None and obj.parent.type == "ARMATURE":
            return obj.parent
    return None


def attach_mesh(mesh_obj, armature_obj):
    """Parent a mesh to an armature (keeping its world transform) and give
    it an armature modifier. Reuses an existing modifier if present."""
    modifier = None
    for mod in mesh_obj.modifiers:
        if mod.type == "ARMATURE":
            modifier = mod
            break
    if modifier is None:
        modifier = mesh_obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature_obj

    if mesh_obj.parent is not armature_obj:
        # parent.matrix_world @ parent_inverse == identity, so the mesh's
        # world transform is untouched by the re-parenting.
        mesh_obj.parent = armature_obj
        mesh_obj.parent_type = "OBJECT"
        mesh_obj.matrix_parent_inverse = \
            armature_obj.matrix_world.inverted_safe()


def attach_to_bone(obj, armature_obj, bone_name):
    """Rigidly follow one bone via a Child Of constraint - no vertex
    groups or armature modifier, since the object itself moves with the
    bone rather than deforming against it. Used for destructible-building
    pieces (matrix_index-driven), which are genuinely rigid/unrigged, not
    skinned - creating a vertex group for them would misreport them as
    weighted for vertex-format/LOD purposes.

    Deliberately does NOT "set inverse": the whole point of matrix_index
    is that the piece belongs at the bone, so the bone's current (bind
    -pose) transform is meant to move it there - same as a single
    full-weight vertex group would, just without the deform machinery.
    `inverse_matrix` is forced to identity so the object's own local
    transform (its file pivot) is simply composed on top of the bone's
    world transform - assigning `target`/`subtarget` alone is NOT enough,
    Blender auto-computes an inverse that cancels the bone's CURRENT
    transform the moment those are set (even through this same Python
    API), which is the opposite of what's wanted here. Reuses an
    existing Child Of constraint targeting this bone if present
    (re-import)."""
    for existing in obj.constraints:
        if (existing.type == "CHILD_OF" and existing.target is armature_obj
                and existing.subtarget == bone_name):
            return
    con = obj.constraints.new("CHILD_OF")
    con.target = armature_obj
    con.subtarget = bone_name
    con.inverse_matrix = Matrix.Identity(4)
