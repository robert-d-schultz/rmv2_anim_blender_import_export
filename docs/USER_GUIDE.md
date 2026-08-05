# Total War RigidModel / .anim Blender Add-on

Import/export Creative Assembly's **.rigid_model_v2** meshes and **.anim** skeletons/animations (Rome 2 to Warhammer 3 era) directly in Blender.

## Install

Grab the zip from the [latest release](https://github.com/robert-d-schultz/rmv2_anim_blender_import_export/releases/latest).

- **Blender 4.2+:** `Edit -> Preferences -> Get Extensions -> Install from Disk`, pick the zip.
- **Blender 3.6–4.1:** `Edit -> Preferences -> Add-ons -> Install`, pick the zip, enable it.

**[screenshot: add-on preferences panel]**

Then set **Texture Root Directory** in the add-on's preferences to a folder of extracted game textures (RPFM/AssetEditor dump). That's what lets imported materials actually show images instead of blank slots.

## Importing models & skeletons

- `File -> Import -> Total War RigidModel (.rigid_model_v2)`: imports a mesh.
- `File -> Import -> Total War Animation (.anim)`: imports a skeleton *or* an animation. Same menu entry for both; the add-on figures out which you mean from the file.

**[screenshot: import menu]**

The part to know: **what's selected before you import decides how things connect.**

- Import a skeleton with nothing relevant selected -> builds a fresh armature.
- Import an animation with that armature (or its model) selected -> keys it on as a new action.
- Import a model with an armature selected -> the meshes get vertex groups named after its real bones and get parented to it, instead of generic placeholder groups.

So model and skeleton can go in **either order**: import one, select the result, then import the other.

## Collections are "the model"

This is the bit that trips up people used to a flatter Blender scene: the add-on treats **collections**, not loose objects, as the real unit.

- One **root collection** = one in-game model (holds skeleton name, file version, attachment points).
- Inside it, one **LOD collection per level of detail**, each holding that LOD's meshes.

**[screenshot: outliner showing root + LOD collections]**

If you're building a model from scratch rather than round-tripping an import, set up that same root -> LOD layout first. Select your mesh(es) and use `Object -> Setup RMV2 LOD Collections` in the 3D viewport (also findable via `F3` search) to build it for you from the selection.

## Exporting

`File -> Export -> Total War RigidModel (.rigid_model_v2)` and `File -> Export -> Total War Animation (.anim)`.

You don't have to select the whole model. Select anything inside a model's root collection (one mesh, the armature, whatever) and export grabs the entire thing, all LODs included.

There's also an auto-LOD option that generates the lower LODs for you instead of requiring hand-built ones. Not covered here, just know it's there.

## Extra properties are tucked into the normal tabs

Meshes, collections, and armatures all get extra RMV2/`.anim` settings added into Blender's regular Properties editor: Object Properties, Collection Properties, and Object Data Properties (for armatures). Nothing exotic, just look for a panel called **RMV2 (RigidModel)** or **RMV2 (.anim)** if a setting you expect isn't obviously on the surface.

**[screenshot: an RMV2 panel in the Properties editor]**
