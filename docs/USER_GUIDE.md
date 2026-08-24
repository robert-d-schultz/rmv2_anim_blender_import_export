# Total War RigidModel / .anim Blender Add-on

Import/export Creative Assembly's **.rigid_model_v2** meshes and **.anim** skeletons/animations (Rome 2 to Pharaoh Dynasties era) directly in Blender, along with the older games' formats.

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

```
model_name                 <- root collection: version, skeleton name, attachment points
├── model_name_lod0        <- LOD collection: camera distance, quality level
│   ├── body_lod0
│   └── head_lod0
├── model_name_lod1        <- hidden on import (eye icon, not excluded)
└── ...
```

**[screenshot: outliner showing root + LOD collections]**

All the formats build this same layout, so the panels and the `.anim` importer work the same whichever one a model came from.

If you're building a model from scratch rather than round-tripping an import, set up that same root -> LOD layout first. Select your mesh(es) and use `Object -> Setup RMV2 LOD Collections` in the 3D viewport (also findable via `F3` search) to build it for you from the selection.

## Exporting

`File -> Export -> Total War RigidModel (.rigid_model_v2)` and `File -> Export -> Total War Animation (.anim)`.

You don't have to select the whole model. Select anything inside a model's root collection (one mesh, the armature, whatever) and export grabs the entire thing, all LODs included.

There's also an auto-LOD option that generates the lower LODs for you instead of requiring hand-built ones. Not covered here, just know it's there.

## Extra properties are tucked into the normal tabs

Meshes, collections, and armatures all get extra RMV2/`.anim` settings added into Blender's regular Properties editor. Look for a panel called **RMV2 (RigidModel)** or **RMV2 (.anim)** if a setting you expect isn't obviously on the surface.

- **Properties -> Object -> RMV2 (RigidModel)** — vertex format, material id, alpha mode, shader, render flag, texture directory and texture slot list, matrix index. **Copy RMV2 Settings to Selected** transfers the lot between objects.
- **Properties -> Collection -> RMV2 (RigidModel)** — on the root: file version, skeleton name, attachment points, auto-LOD override rows. On a LOD collection: level, camera distance, quality level.
- **Properties -> Object Data -> RMV2 (.anim)** (armatures) — skeleton name, `.anim` version, frame rate, flags. These become the export defaults.

**[screenshot: an RMV2 panel in the Properties editor]**

---

# Worked examples

## A Warhammer-era unit

1. `File -> Import -> Total War Animation (.anim)` — pick the model's skeleton from `animations/skeletons/` (e.g. `humanoid01.anim`). This builds the armature.
2. `File -> Import -> Total War RigidModel (.rigid_model_v2)` — the meshes attach to that armature and their vertex groups get real bone names.
3. Import more `.anim` files onto the same armature for animations, each arriving as its own action.

The other order works too: import the model first, then the skeleton — the importer renames the `bone_<i>` vertex groups and parents the meshes retroactively, hidden LOD collections included. If you don't know which skeleton a model wants, its name is on the root collection under `Properties -> Collection -> RMV2`.

## A Shogun 2 unit part

1. `File -> Import -> Total War Animation (.anim)` — import the model's **reference** skeleton from `animations/shogun_animation/.../reference/` (`man_shogun`, `horse`, `deer`, `bird`, `bear`, `whale`, `campaign_ship`).
2. Leave the armature selected.
3. `File -> Import -> Total War Shogun 2 Unit Part (.variant_part_mesh)`.

Import it without an armature and you still get a mesh — the bind pose is reconstructed from the file's own geometry — but it is approximate, bones it can't reach are reported, and the importer warns you. **Export refuses without an armature**, rather than writing something wrong.

Don't know which skeleton? Import the part anyway: the file names it, and the importer tells you (`skeleton: man_shogun`), so you can load the right one and re-import.

## An Empire or Napoleon unit

Empire's units are `.variant_weighted_mesh`, one file per unit per LOD, holding every body part its variants can draw.

1. `File -> Import -> Total War Animation (.anim)` — import `animations/reference/tpose.anim`. Every unit model in both games is rigged to this same 41-bone skeleton, so there is nothing to guess.
2. Leave the armature selected.
3. `File -> Import -> Total War Empire Unit Mesh (.variant_weighted_mesh)` — pick `<unit>_lod1` … `<unit>_lod4`; you can select all four at once.

The `_lodN` suffix is understood, so the four files fill in **one** root collection as LOD 0–3 (CA numbers from 1, Blender from 0) rather than making four unrelated models.

Textures are not named inside the file; they are looked up by convention as `unitmodels/textures/<unit>_diffuse.dds` (and `_normal`, `_gloss_map`) under your Texture Root.

## An Empire destruction prop

`.rigid_model_animation` is self-contained — the `.animatable_rigid_model` object list followed by a whole headerless `.anim` — so there is nothing to import first. `File -> Import -> Total War Animated RigidModel (.rigid_model_animation)` builds the armature from the file, keys its animation as an action, and welds each object to the bone it rides on.

---

# Reference

## Menu entries

Everything lives under `File -> Import` and `File -> Export`:

| Entry | Handles |
| --- | --- |
| Total War RigidModel (.rigid_model_v2) | Every RMV2 era, Shogun 2 included |
| Total War Shogun 2 RigidModel (.animatable_rigid_model, .rigid_model) | Both forms; the extension you pick decides which gets written |
| Total War Shogun 2 Unit Part (.variant_part_mesh) | Skinned parts, rigid equipment, and library files |
| Total War Empire Unit Mesh (.variant_weighted_mesh) | Empire/Napoleon skinned units, one file per LOD |
| Total War Animated RigidModel (.rigid_model_animation) | Objects and the animation that moves them, in one file |
| Total War Animation (.anim) | Skeletons and animations alike |

**One `.anim` entry does both jobs.** Skeleton files are just animations whose two or three frames all repeat the bind pose, so the importer looks at the file and the scene: no armature yet plus a bind-pose-looking file builds the armature; otherwise the frames are keyed onto the existing armature as an action.

## Import options

| Option | Where | Meaning |
| --- | --- | --- |
| Import All LODs | RMV2 | Off (default): finest LOD only. On: every LOD in its own collection |
| LODs | Unit Part | *All*, or *Most detailed only* |
| Build Materials | RMV2, Shogun 2 RigidModel, Unit Mesh, Animated RigidModel | Build Principled BSDF node trees from the file's textures |
| Texture Root | as above | Override the preferences folder for this import |
| Attachment Point Empties | RMV2 | Create empties for the file's attachment points |
| Attach To Selected Armature | RMV2, Shogun 2 RigidModel, Unit Part | Bind to a selected armature and use its bone names |
| Attach Meshes | .anim | Rename `bone_<i>` groups and parent the model's meshes |
| Import Frames As Action | .anim | Also key the frames when the file is used to build a fresh armature |
| Scale | all | Uniform scale — keep it the same across a model and its `.anim` |

Multi-file selection works for RMV2, Shogun 2 RigidModel, Unit Part, Unit Mesh and Animated RigidModel.

## Export options

| Option | Where | Meaning |
| --- | --- | --- |
| Source | all meshes | *Auto* (active model, else selection), *Selected*, *Visible* — plus *Active Collection* and *Batch* (one file per RMV2 collection in the scene) for RMV2 |
| Version | RMV2, Shogun 2 RigidModel, .anim | Target file version — the RigidModel entry spans 5/4/3 and the older 1 and headerless-0 objects |
| Skeleton | RMV2, Unit Part, .anim | Header skeleton name; defaults to what was stored at import |
| Generate LODs (Decimate) | RMV2 | Build the lower LODs from the finest by decimation instead of using LOD collections |
| Apply Modifiers | all meshes | Export the evaluated mesh. Armature deform is *always* excluded, so the rest pose is written |
| High Precision Positions | RMV2 | CA's half-float `W`-scale trick for extra precision (slower) |
| Attachment Points | RMV2 | Write them, from the collection's list or the armature's bones |
| Mode | .anim | *Animation* samples the scene frame range; *Bind Pose (Skeleton)* writes the rest pose |
| Frame Rate | .anim | 0 = use the armature's stored rate, else the scene's |
| LOD Level | Unit Mesh | Which level to write — one file is one LOD in that format, so a full ladder is four exports |
| Frame Start / End | Animated RigidModel | Range sampled for the embedded animation |

## Things that will trip you up

**"Nothing to export."** Make the model's collection active in the outliner, or select the meshes. For `.variant_part_mesh` **libraries**, use the collection — non-finest LODs import hidden, so a selection would only catch LOD 0.

**Unit-part export refuses without an armature.** By design: the format has nowhere to put a vertex except in a bone's space. Import the reference `.anim` and attach the meshes. If the meshes really are unskinned props, set their *Vertex Format* to **Variant Part Rigid** in the Object panel.

**Vertex counts change.** Vertices are welded at the file's own precision, and hard edges and UV seams split them — so counts can differ from Blender's in both directions. The hard ceiling is **65,536 vertices per mesh** (16-bit indices); past that, split the object.

**Skinned export needs resolvable bone indices.** Keep the vertex group names the importer made (`bone_12`-style, or real bone names after a `.anim` import), or use an armature whose bone order matches the game skeleton. Bone *order* is what the game cares about — armatures this add-on creates stamp each bone with an `rmv2_bone_index` custom property, so renaming and reordering bones in Blender is safe.

**Textures don't show up.** Set the **Texture Root Directory** in the add-on preferences to your extracted-texture folder. The file stores paths, not images.

**Scale must match.** If you import a model at a non-default scale, import its `.anim` at the same one.

**Moving an object moves the file's pivot.** RMV2 vertices are stored relative to a pivot the game adds back at render time, so the importer puts the pivot at the object's origin. On export the object's world *translation* becomes the pivot; rotation and scale are baked into the vertices. (Shogun 2 has no pivot field — its meshes live in bone space — so there the translation goes into the vertices too.)

**"This file is for skeleton X."** The armature remembers which skeleton it was built from and refuses an animation meant for a different one, so you find out immediately rather than after the pose comes out mangled.

**A model gets exactly one armature.** Importing a second skeleton onto a model that already has one is an error, not a silent second rig. To rig a *different* model with the same skeleton, select that model's meshes or collection first.

**Shogun 2 has no separate skeleton files.** Every `.anim` carries the full bone table, and models name the `.anim` they belong to rather than the other way round.

**Empire unit meshes need `tpose.anim` first.** `.variant_weighted_mesh` does not even name its skeleton — it does not have to, since every unit in Empire and Napoleon uses the same 41-bone rig at `animations/reference/tpose.anim`. Import that, leave it selected, then import the mesh.

**One `.variant_weighted_mesh` is one LOD.** The ladder is four separate files, so a full export is four exports with *LOD Level* set to 0, 1, 2, 3 — writing files named `<unit>_lod1` … `<unit>_lod4`, the way CA numbers them. On import the suffix is read back and the four files share a single root collection.
