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

### The older games are the exception

Shogun 2's unit parts (`.variant_part_mesh`) and Empire/Napoleon's unit meshes (`.variant_weighted_mesh`) store **no model-space positions at all** — every vertex is held in its bones' spaces — so for those the skeleton is not optional and the order is fixed: **skeleton first, then the mesh.**

- Shogun 2: the reference `.anim` under `animations/shogun_animation/.../reference/`. The file names the one it wants, and the importer tells you which.
- Empire and Napoleon: always `animations/reference/tpose.anim`. Every unit in both games uses that same 41-bone rig, so there's nothing to look up.

Import one of those meshes without an armature selected and you get an error saying exactly which file to load first, rather than a model collapsed onto the origin.

One Empire/Napoleon file is the exception to the exception: `unitmodels/euro_equipment.variant_weighted_mesh` holds no skinned parts at all, only the 134 props units carry — every musket, spear and flagpole in the game, each welded to one bone. It imports with or without a skeleton (with one, each prop is bound to its bone). Ordinary unit files can carry props of their own alongside their body parts; they arrive as rigid objects with a Child Of constraint and no vertex groups, and go back out the same way.

`.rigid_model_animation` is the opposite case: the skeleton and the animation are *inside* the file, so there's nothing to import first.

## Versions

Every version the add-on reads, it also writes, and the **Version** dropdown in each exporter lists all of them. You don't normally need to touch it: importing a file records its version on the model's root collection (`.anim` records it on the armature), and the exporter opens on that — so re-exporting something you imported keeps it as it was.

Change it when you're moving a model between games. Import a Warhammer 3 mesh, set **Version** to *RMV2 v1* and it's written in Shogun 2's layout; set an armature's export to *Anim v1 (Shogun 2)* and you get a Shogun 2 animation. A model built from scratch starts on what Warhammer 3 most commonly ships (RMV2 v8, `.anim` v8).

Going backwards can cost you something the older format has no room for — an Empire-era rigid-model object holds one texture name where a modern one holds four. The exporter writes what fits and tells you what it left out, rather than refusing.

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
