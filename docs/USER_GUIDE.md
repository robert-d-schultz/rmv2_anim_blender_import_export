# Total War RigidModel / .anim Blender Add-on

Import/export Creative Assembly's **.rigid_model_v2** meshes and **.anim** skeletons/animations (Rome 2 to Pharaoh Dynasties era) directly in Blender, along with the older games' formats.

## Install

Grab the zip from the [latest release](https://github.com/robert-d-schultz/rmv2_anim_blender_import_export/releases/latest).

- **Blender 4.2+:** `Edit -> Preferences -> Get Extensions -> Install from Disk`, pick the zip.
- **Blender 3.6–4.1:** `Edit -> Preferences -> Add-ons -> Install`, pick the zip, enable it.

**[screenshot: add-on preferences panel]**

Then set **Texture Root Directory** in the add-on's preferences to a folder of extracted game textures (RPFM/AssetEditor dump). That's what lets imported materials actually show images instead of blank slots.

## Importing models & skeletons

- `File -> Import -> Total War RigidModel v2 (.rigid_model_v2)`: imports a mesh.
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

Every version the add-on reads, it also writes. **There is no version field in any export dialog**: a model is one file and a file has one version, so the version lives on the thing it describes — the model's root collection (`Properties -> Collection -> Total War Settings`), or the armature for `.anim` (`Properties -> Object Data -> Total War Settings`). Import records it there; export reads it back. Re-exporting something you imported keeps it as it was, without your having to remember anything.

The collection's **Version** is one dropdown covering every container the add-on writes, grouped by format: `.rigid_model_v2`, `.animatable_rigid_model` / `.rigid_model`, `.variant_part_mesh`, `.variant_weighted_mesh`. It says which file the model came from as well as which version of it. Export to a *different* container and that exporter falls back to that container's default, since a `.rigid_model_v2` version has nothing to say about a `.variant_part_mesh` one.

Because the version rides the collection, a **batch export** writes each collection as its own version in one go — which a single dialog field could never express.

Change it when you're moving a model between games. Import a Warhammer 3 mesh, set the collection's **Version** to *RMV2 v1* and it's written in Shogun 2's layout; set an armature's to *Anim v1 (Shogun 2)* and you get a Shogun 2 animation. A model built from scratch starts on what Warhammer 3 most commonly ships (RMV2 v8, `.anim` v8).

## The panels follow the format

The Total War Settings panels only show what the model's own format has somewhere to put. Setting a field the exporter would silently drop is worse than not being offered it, so:

| Not offered | Where | Because |
| --- | --- | --- |
| Quality Level | RMV2 before v7 | The LOD header is 20 bytes and stops after the camera distance; v7 added the level and quality bytes |
| Attachment Points | RMV2 v1 – v3 | Shogun 2's material is a run of fixed-width strings with no room for them |
| Alpha, Filters, Collider Bone, Texture Directory | RMV2 v1 – v3 | Same — the modern material's tagged parameter lists don't exist yet |
| Shader Name | RMV2 v1 | Arrived in v2 |
| Material Id, Alpha, Shader Name, Filters, Vertex Format | `.animatable_rigid_model` / `.rigid_model` | A flat object list: four texture slots, a bone index and a shader parameter block, nothing else |
| Shader Parameters | `.animatable_rigid_model` object versions 0 – 3, and RMV2 | The block arrives with ARM v4. RMV2 has parameters too, but addresses them by index rather than by name, so they are preserved rather than shown |
| Texture slots, Material Id | `.variant_part_mesh` | Its material *is* three names, shown under **Material** on the mesh (or on the model, for a non-library file). There are no textures to slot |
| Most Vertex Formats | `.variant_part_mesh` | It has three layouts of its own, chosen by its header. Offering a variant part *Sway* offers it a number its format cannot hold |
| Texture slots, Material Id | `.variant_weighted_mesh` | The part name *is* the texture set. An unrigged part is an attachment, which is an ARM object, so that one keeps its slots |
| Auto-LOD Overrides | `.animatable_rigid_model` / `.rigid_model`, `.variant_weighted_mesh` | The decimator is Blender's own and runs on anything; what decides it is whether the container has a ladder for the generated levels. `.rigid_model` has none, and the other two keep theirs across separate `_lod1`/`_lod2` **files** — generating one means writing four files, a different feature |
| Camera Distance | `.variant_part_mesh`, and formats with no ladder | The container has nowhere to keep one |
| LOD Level | `.animatable_rigid_model` / `.rigid_model` | One file is one LOD, so the collection says so instead of offering a number with nothing to vary. `.variant_weighted_mesh` *does* keep one — its levels are separate `_lod1`..`_lod4` files that import into one model |

### Vertex Format is per mesh, and follows the container

**Vertex Format is a mesh setting, not a model setting**, because that is where the file keeps it. Vanilla models mix freely: Warhammer 3 ships units that are Cinematic up close and Weighted at distance, `maple_d` is Vegetation plus one Tree Billboard card, and `wef_oak_of_ages_part_b` is Static plus six Sway meshes.

The dropdown is narrowed to what the model's **container** can store — an RMV2 mesh sees the RMV2 layouts, a `.variant_part_mesh` mesh sees only its own three. It is *not* narrowed by version, so an RMV2 model lists the Shogun 2 layouts whatever version it is. That is not an oversight: `army_banner_flag_general` is a v7 file storing the Shogun 2 layout, and `zen_garden_floor_stone_01` is a v1 file storing the modern Static one, so neither set belongs to one era. Working out the real per-version table needs a corpus census of several games — see TODO 9.

The **Auto-LOD Override** rows have a Vertex Format column too, and it follows the same list.

### Neither ARM format names its skeleton

`.animatable_rigid_model` and `.rigid_model` have no skeleton field
anywhere, so the importer will not invent one:

- A plain `.rigid_model` carries no bone index on any object — that
  is the whole of what "not animatable" means — so its **Skeleton**
  is left empty. It is scenery; it rides nothing.
- An `.animatable_rigid_model` does ride a skeleton, but the file does
  not say which. CA's convention is an `.anim` of the same name sitting
  beside it, so that name is used **only when that file actually
  exists**. Otherwise you get an empty Skeleton and a warning, and can
  fill it in yourself on the model collection.

Filling it in regardless is what used to put `mountainb.anim` on a
Napoleon campaign mountain that has no bones at all.

`.rigid_model_animation` is different — it carries its skeleton
inside the same file, so it is self-contained and names itself.

### A library .variant_part_mesh opens as one prop

Vertex format 2 packs many unrelated props into a single file —
`equipment/mesh1` holds 52 of them across 142 parts — and every one
sits at the origin, so showing them all is a heap rather than a model.
These import with **only the first object visible**; the rest have their
viewport eye closed. Nothing is deleted and export still writes every
part.

The usual "hide all but the finest LOD" pass is skipped for these, since
it would only take away the other levels of the prop you are looking at.
Files that are not libraries are unchanged: their parts *are* a LOD
ladder, so the finest level stays shown and the rest are hidden.

One catch: Blender will not select a hidden object, so **Selected
Objects** on a freshly imported library exports just the visible prop.
The default export source reads the collection instead and writes the
lot; open the eyes first if you want to work from the selection.

### A .variant_weighted_mesh ladder is several files

CA ships a unit as `<unit>_lod1` to `<unit>_lod4` — one file per
level, with the level in the **filename**, not in the file. Import them
together (or one after another) and they land in a single model, one LOD
collection each, with CA's `_lod1` becoming Blender's LOD 0. That merge
happens when a model root of that name already exists in the scene;
nothing goes looking on disk for the other files.

Export mirrors it. **Write All LODs** is on by default and writes one
file per LOD collection, named the way CA does — pick
`myunit.variant_weighted_mesh` and you get `myunit_lod1` and
`myunit_lod4` for a model with those two levels. Any `_lodN` you type
yourself is stripped first, so picking `myunit_lod1` writes the same set.

Turn it off to write a single level, chosen with **LOD Level**. You get a
warning naming the levels that were left behind, because one file is one
LOD and it is otherwise easy to think a whole model was exported.

### Textures the file doesn't name

`.variant_weighted_mesh` names no textures at all — the part name selects the texture set, and the importer looks for them under `unitmodels/textures/`. If none are found you get a warning rather than dead image nodes. Point it at your extracted textures with **Texture Root Override** in the import dialog (it appears under Build Materials), or set it once in **Edit → Preferences → Add-ons → Total War Model … → Texture Root Directory**. The dialog field is only an override for that one import — leave it empty and the dialog tells you which preference it is falling back to.

`.variant_part_mesh` has no textures in the file either, and no slots to put any in — its material is three names. That is the format, not a failed import.

Changing the Version to a different *container* converts the meshes: a format the new container has no number for — a `.variant_part_mesh` layout in a model now being written as `.rigid_model_v2` — becomes **Auto**, which is the exporter working it out from the mesh. Moving between versions of the *same* container never touches it, so going v7 → v8 leaves a deliberate Sway or Weighted exactly as you set it.

A mesh not yet inside a model root is offered everything, which is the state importers build meshes in. A mesh dragged from one model into another keeps the format it came with and has it pinned onto the new list, since nothing fires on a move to convert it.

### Shader parameters

Shogun 2 and Empire's containers carry a block of named values — `light_scale`, `bumpfactor`, `specfactor` — and in the formats with no material id and no texture slots that block *is* the material. They import as an editable list under **Shader Parameters**, and export writes back exactly what is in it.

Which panel shows the list is a fact about the container, not a layout choice:

| Format | Where the block lives | Panel |
| --- | --- | --- |
| `.animatable_rigid_model` / `.rigid_model` | One per object, from object version 4 | The mesh's Total War Settings |
| `.variant_part_mesh` | One for the whole file | The root collection's Total War Settings |
| `.variant_weighted_mesh` | One for the whole file | The root collection's Total War Settings |

Objects in one ARM file genuinely differ — Shogun 2's `naval_cannon_12lb_lod4` has 13 parameters on three of its four objects and none at all on the fourth — so an object that came in with no parameters goes back out with none. A mesh built in Blender gets the block CA writes for a plain lit object instead, because a file with no parameters at all renders unlit.

The block has two lists and the file keeps them apart, so the **Type** column is not a display choice. Single values show as a number; the four-component ones are RGBA colours — `colourmapfactor`, `rimcolor`, `specfactor` are the only ones in the corpus and all three are colours — so they get a swatch, with R/G/B/A fields under the list for typing exact values. The swatch shows the stored numbers directly rather than gamma-correcting them, and the field has no 0–1 limit, so a file holding a value outside that range keeps it.

The parameter names are whatever the game's shader reads; the file carries no list of the valid ones, so **Add** starts a row at `light_scale` and the name is yours to set.

Every format goes through the same root-collection-of-LOD-collections shape even when its file has no LOD ladder — `.animatable_rigid_model`, `.variant_weighted_mesh` and `.rigid_model_animation` all get a dummy LOD 0 — so the panels, the `.anim` importer and the exporters work the same way whatever the model came from.

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

**There is one checkbox, Model Root.** A collection is a LOD because it sits inside a root, not because anything is ticked on it — so drop a collection into a model and it starts showing LOD settings, and drag it out and it stops. There is no way to make something a root *and* a LOD, because there is nothing to set. Ticking Model Root on a collection that is inside another model makes it its own model, and its parent stops counting it as a level.

**LOD Level** is normally filled in for you. While it is still 0, a number in the collection's name is used instead — so a ladder built by hand as `model/model_lod0..3` works without opening the panel at all.

If you're building a model from scratch rather than round-tripping an import, set up that same root -> LOD layout first. Select your mesh(es) and use `Object -> Setup Total War LOD Collections` in the 3D viewport (also findable via `F3` search) to build it for you from the selection.

## Exporting

`File -> Export -> Total War RigidModel v2 (.rigid_model_v2)` and `File -> Export -> Total War Animation (.anim)`.

You don't have to select the whole model. Select anything inside a model's root collection (one mesh, the armature, whatever) and export grabs the entire thing, all LODs included.

There's also an auto-LOD option that generates the lower LODs for you instead of requiring hand-built ones. Not covered here, just know it's there.

## Extra properties are tucked into the normal tabs

Meshes, collections, and armatures all get extra settings added into Blender's regular Properties editor. Look for a panel called **Total War Settings** if a setting you expect isn't obviously on the surface — same title in all three places, and each shows only what the model's own format has room for (see *The panels follow the format* above).

- **Properties -> Object -> Total War Settings** — vertex format, material id, alpha mode, shader, render flag, texture directory and texture slot list, bone index. **Copy Total War Settings to Selected** transfers the lot between objects.
- **Properties -> Collection -> Total War Settings** — on the root: which file the model came from and its version (one dropdown, grouped by format), skeleton name, attachment points, auto-LOD override rows. On a LOD collection — which is any collection inside a root — level, camera distance, quality level.
- **Properties -> Object Data -> Total War Settings** (armatures) — skeleton name, `.anim` version, frame rate, flags. Export reads these; there is no version field in the `.anim` export dialog.

**[screenshot: a Total War Settings panel in the Properties editor]**

---

# Worked examples

## A Warhammer-era unit

1. `File -> Import -> Total War Animation (.anim)` — pick the model's skeleton from `animations/skeletons/` (e.g. `humanoid01.anim`). This builds the armature.
2. `File -> Import -> Total War RigidModel v2 (.rigid_model_v2)` — the meshes attach to that armature and their vertex groups get real bone names.
3. Import more `.anim` files onto the same armature for animations, each arriving as its own action.

The other order works too: import the model first, then the skeleton — the importer renames the `bone_<i>` vertex groups and parents the meshes retroactively, hidden LOD collections included. If you don't know which skeleton a model wants, its name is on the root collection under `Properties -> Collection -> Total War Settings`.

**Either order works for rigid pieces too, in every format.** A piece that rides one bone whole rather than deforming — a destructible building's chunk, a Shogun 2 cannon barrel, a `.variant_part_mesh` helmet, a `.variant_weighted_mesh` musket — keeps its bone index under **Advanced -> Bone Index** while there's no armature, and the `.anim` import welds it on with a Child Of constraint when one arrives. Import the armature first instead and it happens immediately. Either way the constraint becomes the only record of which bone it rides, and export reads it back from there, so re-targeting the constraint in Blender is what moves the piece.

A mesh with vertex groups is never caught by that — having none is what marks a piece as rigid rather than skinned.

**Collider Bone** is the other way a mesh can name a bone. A cloth cloak carries `collider_*` proxy meshes that the physics sim bounces the cloth off, and each one names the bone it follows there rather than in Bone Index. In Warhammer 3 that is the only thing that ever uses the field: 81 meshes, all colliders, all in the six `*_cloth_cloak_01` models.

## A Shogun 2 unit part

1. `File -> Import -> Total War Animation (.anim)` — import the model's **reference** skeleton from `animations/shogun_animation/.../reference/` (`man_shogun`, `horse`, `deer`, `bird`, `bear`, `whale`, `campaign_ship`).
2. Leave the armature selected.
3. `File -> Import -> Total War, pre-Rome 2 -> Variant Part Mesh (.variant_part_mesh)`.

Import it without an armature and you still get a mesh — the bind pose is reconstructed from the file's own geometry — but it is approximate, bones it can't reach are reported, and the importer warns you. **Export refuses without an armature**, rather than writing something wrong.

Don't know which skeleton? Import the part anyway: the file names it, and the importer tells you (`skeleton: man_shogun`), so you can load the right one and re-import.

**Generate LODs works here too.** A `.variant_part_mesh`'s parts *are* its LOD ladder, the same as `.rigid_model_v2`'s LOD table, so the export dialog has the same **Generate LODs (Decimate)** switch and reads the same Auto-LOD Override rows off the root collection. The exception is library files, whose parts are named individually in the file: a generated level would write the same stored name twice, so it's refused with a message rather than producing a broken file. Set those up as LOD collections by hand.

**Rigid parts** — equipment, crests, the `variantmodels/equipment/` libraries — aren't skinned. Each one names a single bone it rides and stores its vertices in that bone's space, so it arrives with a Child Of constraint instead of vertex groups, exactly like an `.animatable_rigid_model` object. Re-target that constraint to move a prop to a different bone; export reads it back.

Several of the mounts are *attachment* bones — `Weapon1`, `Weapon2`, `Weapon3` on `man_shogun` — which sit at the world origin in the reference pose and are placed by whatever animation is playing. A prop on one of those sitting at the origin in the T-pose is correct, not a failed import; load an animation and it goes where the animation puts it.

## An Empire or Napoleon unit

Empire's units are `.variant_weighted_mesh`, one file per unit per LOD, holding every body part its variants can draw.

1. `File -> Import -> Total War Animation (.anim)` — import `animations/reference/tpose.anim`. Every unit model in both games is rigged to this same 41-bone skeleton, so there is nothing to guess.
2. Leave the armature selected.
3. `File -> Import -> Total War, pre-Rome 2 -> Variant Weighted Mesh (.variant_weighted_mesh)` — pick `<unit>_lod1` … `<unit>_lod4`; you can select all four at once.

The `_lodN` suffix is understood, so the four files fill in **one** root collection as LOD 0–3 (CA numbers from 1, Blender from 0) rather than making four unrelated models.

One file holds every alternative the unit's variants can pick between — four heads, two bodies, two pairs of legs — all standing in the same place. **One Variant Per Slot** (on by default) leaves the first of each set visible and closes the viewport eye on the rest, so the model opens as one soldier rather than a ball of overlapping geometry. Nothing is deleted, the eye icon brings the others back, and export writes them all regardless.

Nothing in the file marks which parts are alternatives for the same slot: the part table is flat and the parts carry no slot field. They are grouped the only way the file allows, which is also the way CA's own variant tables address them — by name up to a trailing number, so `<unit>_head01` … `<unit>_head04` are one slot and `Telescope` is a slot of one.

Textures are not named inside the file; they are looked up by convention as `unitmodels/textures/<unit>_diffuse.dds` (and `_normal`, `_gloss_map`) under your Texture Root.

## An Empire destruction prop

`.rigid_model_animation` is self-contained — the `.animatable_rigid_model` object list followed by a whole headerless `.anim` — so there is nothing to import first. `File -> Import -> Total War, pre-Rome 2 -> RigidModel Animation (.rigid_model_animation)` builds the armature from the file, keys its animation as an action, and welds each object to the bone it rides on.

---

# Reference

## Menu entries

Everything lives under `File -> Import` and `File -> Export`:

| Entry | Handles |
| --- | --- |
| Total War RigidModel v2 (.rigid_model_v2) | Every RMV2 era, Shogun 2 included |
| Total War Animation (.anim) | Skeletons and animations alike |

The four formats only the older games use sit one level down, under **Total War, pre-Rome 2**:

| Entry | Handles |
| --- | --- |
| Animatable RigidModel (.animatable_rigid_model) | Jointed props; on export each object carries the index of the bone it rides |
| RigidModel (.rigid_model) | Static geometry; on export no bone indices, a bounding box instead |
| Variant Part Mesh (.variant_part_mesh) | Shogun 2 skinned parts, rigid equipment, and library files |
| Variant Weighted Mesh (.variant_weighted_mesh) | Empire and Napoleon skinned units, one file per LOD |
| RigidModel Animation (.rigid_model_animation) | Empire and Napoleon objects with the animation that moves them, in one file |

**The two RigidModel rows mean different things on the way in and out.** Exporting, they are a real choice — the two forms differ by whether each object carries a bone index. Importing, they are only a browser filter: one operator reads both, and which form a file is gets decided by looking inside it, not by the row you clicked or the name it has. So either row will happily open either file.

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
| One Variant Per Slot | Unit Mesh | On (default): show the first of each set of alternatives (`head01`..`head04`) and close the eye on the rest. Nothing is deleted |
| Attach Meshes | .anim | Rename `bone_<i>` groups and parent the model's meshes |
| Import Frames As Action | .anim | Also key the frames when the file is used to build a fresh armature |
| Scale | all | Uniform scale — keep it the same across a model and its `.anim` |

Multi-file selection works for RMV2, Shogun 2 RigidModel, Unit Part, Unit Mesh and Animated RigidModel.

## Export options

| Option | Where | Meaning |
| --- | --- | --- |
| Source | all meshes | *Auto* (active model, else selection), *Selected*, *Visible* — plus *Active Collection* and *Batch* (one file per RMV2 collection in the scene) for RMV2 |
| Skeleton | RMV2, Unit Part, .anim | Header skeleton name; defaults to what was stored at import |
| Generate LODs (Decimate) | RMV2, Unit Part | Build the lower LODs from the finest by decimation instead of using LOD collections. Not for `.variant_part_mesh` library files, whose parts are named individually |
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
