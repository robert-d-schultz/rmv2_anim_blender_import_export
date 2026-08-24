# Total War model & animation formats for Blender

Import and export Creative Assembly's model and animation files directly
in Blender — meshes, skeletons, animations, skinning, LODs and materials.

Covers the modern **RMV2** era (Rome 2 → Warhammer 3), **Shogun 2**, and
**Empire / Napoleon** — the older games' formats are different enough to
be a separate job each and are absent from the C# reference everything
else here is built against.

| | |
| --- | --- |
| **Blender** | 4.2+ as an extension, 3.6+ as a legacy add-on. Developed against 5.2 |
| **Install** | [Latest release](https://github.com/robert-d-schultz/rmv2_anim_blender_import_export/releases/latest) → see [Installation](#installation) |
| **Getting files** | Extract with [RPFM](https://github.com/Frodo45127/rpfm) |

## What it supports

| Format | Games | Import | Export |
| --- | --- | --- | --- |
| `.rigid_model_v2` | Rome 2 → Warhammer 3 | v5, v6, v7, v8 | same |
| `.rigid_model_v2` | Shogun 2, and Rome 2's UI models | v1, v2, v3 | same |
| `.anim` | Rome 2 → Warhammer 3 | v4, v5, v6, v7, v8 | same |
| `.anim` | Shogun 2 / Empire / Napoleon | v1 + a headerless variant | same |
| `.animatable_rigid_model` | Shogun 2 / Empire / Napoleon | v0–v5 | same |
| `.rigid_model` | Empire / Napoleon / Shogun 2 | v0–v5 | same |
| `.variant_part_mesh` | Shogun 2 | v0, v2, v3 | same |
| `.variant_weighted_mesh` | Empire / Napoleon | v1 + a headerless variant | same |
| `.rigid_model_animation` | Empire / Napoleon | v3, v4, v5 | same |

**Every version in that table reads *and* writes**, including the ones
AssetEditor will not write: `.anim` v8, whose per-bone packing is
reproduced rather than approximated, and RMV2 v5 — nor the two versions
Rome 2 shipped with and dropped during its own run, RMV2 v3 and `.anim`
v4, which are in no reference this project knows of. The exporter offers
the whole list, so a model imported from one game can be written for
another — pick "Anim v1 (Shogun 2)" or "Version 1 (Empire/Napoleon)" and
that is what lands on disk.

Which version an exporter starts on is not a guess: the importers record
it on the model's root collection (or on the armature, for `.anim`), and
the export operator opens on that. A model built from scratch starts at
what Warhammer 3 most commonly ships — RMV2 v8 (65% of its meshes) and
`.anim` v8 (76% of its animations). Where an older version has nowhere
to put something — a Empire-era object has one texture slot, not four —
the export says what it dropped rather than refusing.

**Per mesh** you get positions, custom split normals, the full tangent
basis, both UV channels, vertex colours and bone weights. **Every vertex
layout the add-on reads, it also writes** — the skinned ones, collision,
`Position16`, both custom-terrain layouts, Shogun 2's four, the five
Rome 2 and Attila added for trees, their billboards, grass, water planes
and terrain tiles, and Warhammer's sway vertex.

Some of those layouts carry per-vertex data no ordinary mesh has a slot
for: a vegetation vertex stores the rest position its branch sways from
and eight wind weights, a bow wave stores a second position for where
the crest travels to, custom terrain has two spare colour channels.
Those ride through Blender as **point attributes** — `rmv2_pivot`,
`rmv2_wind_0`, `rmv2_pos2` and so on — visible in the spreadsheet,
editable, and read back on export.

Warhammer's sway vertex needs none of that — everything it stores lands
on an ordinary mesh field — but it is worth knowing where: its UV is
split across the W components of two half4s, and the **sway weight is
the vertex colour's alpha**, so how far a leaf or a hanging cloth moves
in the wind is something you can paint.

The same goes for the material types that are not the weighted layout:
a decal, a terrain tile, a bow wave or an interface banner imported from
a file is exported as what it was, not flattened into an ordinary
material.

**Round-trip fidelity.** Re-saving an unmodified file byte-for-byte is a
tested invariant, checked against every file the games ship:

| Format | Vanilla files re-saved byte-identically |
| --- | --- |
| Shogun 2 `.anim` | 4249 / 4249 |
| Shogun 2 `.rigid_model_v2` | 2466 / 2470 |
| `.variant_part_mesh` | 562 / 562 |
| `.animatable_rigid_model` + `.rigid_model` | 4257 / 4257 (Empire), 4904 / 4904 (Napoleon) — plus 9 / 9 `testdata` in each |
| `.anim` | 3758 / 3758 (Empire), 4027 / 4027 (Napoleon) |
| `.variant_weighted_mesh` | 1045 / 1045 (Empire), 286 / 286 (Napoleon) — every file including `testdata` |
| `.rigid_model_animation` | 725 / 725 (Empire), 784 / 784 (Napoleon) |
| Rome 2 `.anim` | 6012 / 6012 — every one, v4 and v5 |
| Rome 2 `.rigid_model_v2` | 14 452 / 14 673 |
| Attila `.anim` | 6225 / 6225 |
| Attila `.rigid_model_v2` | 9971 / 10 015 |
| Warhammer `.anim` | 6975 / 6975 — every one |
| Warhammer `.rigid_model_v2` | 9335 / 9335 — every one |
| Warhammer 3 `.anim` | 34 997 / 34 997 — every one, v5, v7 and v8 |
| Warhammer 3 `.rigid_model_v2` | 21 639 / 22 230 — every one of the 591 that do not is a `tree_billboard_material` |

Version 8 nearly did not make that bar, and the reason is worth knowing.
Its byte-packed channels decode as `base + (byte / 127) × scale`, and
Warhammer 3 has bones whose base dwarfs their scale — 0.7071 against
4.2e-06, a byte step of 3.3e-08 where float32 resolves 6e-08. The
decode ran in float32, so adjacent bytes collapsed onto one value before
it ever reached an array, and about 1.6% of files could not be written
back as they were read. Decoding those channels at float64 fixes it
completely; v8 frames are the one layout held at that width, since every
other quantization here (int16 / 32767) fits float32 exactly.

Getting there took four writer fixes, three of them a field that had been
assumed constant:

- The u32 after the `.anim` version is *not* the 1 AssetEditor takes it
  for. Across Warhammer 3's 34984 animations, 51 carry 2 and 41 carry 0;
  Three Kingdoms uses all three values freely.
- Quaternions were clipped to ±1.0 *before* being scaled to int16, which
  turned a stored -32768 into -32767. Clamping the scaled value instead
  keeps the extreme and still guards the overflow.
- A part holding its pose entirely in the static frame follows it with
  `duration × fps + 1`, not the 3 it usually reads as.

Rome 2 turned out to be two games in one. It shipped with **RMV2 v5**
and **`.anim` v4**, both of which write every fixed-width string as
UTF-16 at twice the width, then switched mid-life to v6 and v5, which
are the same layouts in UTF-8 — the switch this add-on had only ever
seen from the far side. Version 4 goes further back still: it keeps
frames the way Shogun 2 does, every bone in every frame as float32,
with two per-bone bitfields where v5 keeps its mapping tables. And v3,
the version Rome 2's 3D interface models use, is not a Rome 2 layout at
all — it is Shogun 2's, carried forward one release.

Attila is the same game format-wise, down to shipping Rome 2's
animation files unchanged, so what it was good for was **materials**.
Every era has a set of them with short fixed headers of their own, and
until now anything that was not the weighted, custom-terrain or
terrain-tile material was read with the weighted layout and ran off the
end of a much shorter header. Now decoded:

- **Terrain tiles**, whose id is 66 or 97 here and 101 in Warhammer, and
  which carry five trailing words or six depending on which.
- **The projected-decal family** — 67, then 87, then 95 — a texture path
  followed by one, nine or ten floats.
- **Materials with no header at all**: bow waves and one of the
  terrain-tile ids go straight from the common header to the vertices,
  so the vertex layout has to come from the stride.
- **Cloth, rope and collision shapes**, which are an ordinary weighted
  material followed by a block of their own. That block is simulation
  data — constraint pairs and rest lengths — and nothing in Blender
  could rebuild it, so it is kept exactly as read and written back with
  the material. The same goes for the extra indices Attila's ropes put
  after their index block.

The same work moved Warhammer 3, which shares most of those materials.
Swept in full afterwards, it stands at 21 639 of 22 230 meshes, and
**every single one of the 591 that fail is a `tree_billboard_material`**
— the one material class left in that game, and now the whole of its
gap. Its animations are complete: 34 997 of 34 997, v8 included.

**Warhammer** is a quiet game by comparison — one mesh version and one
animation version, RMV2 v7 and `.anim` v5, with 25 v6 meshes left over —
and it reads completely: all 9335 models and all 6975 animations. Two
things in it were new:

- **The sway vertex.** 255 of its models — hanging cloth, bone cages,
  leaf cards, a tree's canopy — declare vertex format **12**, a number
  nothing else in the series uses and no reference names. It is 20
  bytes: two half4s whose W components carry the UV between them, and a
  colour whose alpha is the wind-sway weight. Unlike every other half
  position in the format the XYZ is *not* scaled by W, because W is the
  U coordinate; what settles it is that adding the material's pivot to
  the raw half3 lands exactly on the header's own bounding box.
- **The interface-banner material** (ids 29 and 30, 288 bytes), which
  was on the list below and is now off it: a 256-byte model name and
  eight words — zero in all 19 of them — on 28-byte vertices its header
  does not describe at all.

What is left is a shorter tail: point lights, a few statues and Attila's
night lights. Their headers are all "a name or path, then a run of
words", but every vanilla example has those words at zero, so there is
nothing to check a guess against.

Two counts above are older than the banner material and are quoted as
they were measured: the 44 unreadable files in Attila and 221 in Rome 2
included their banners, and so do the Rome 2 and Attila rows in the
table. Both games have since come off this disk, so the honest thing is
to leave the numbers as they were taken rather than adjust them by
arithmetic.

That is **every** Empire and Napoleon file of every supported format,
with one exclusion: Empire's 224 **Elite Units DLC**
`.variant_weighted_mesh` files, which ship under a per-file cipher.
Napoleon's are not encrypted. CA's own `testdata/` folder is excluded
from the `.anim` counts above — it holds half-finished exports from
formats that never shipped — though the rigid models and weighted meshes
in it now round-trip too.

Modern RMV2 and `.anim` v5–v7 round-trip byte-identically as well.
Obscure fields you never see — padding bytes, shader parameter blocks,
raw transform matrices — are preserved rather than regenerated. The RMV2
writer also re-parses its own output before it touches disk, so a file
that would not load back is never written.

## Installation

Download the zip from the
[latest release](https://github.com/robert-d-schultz/rmv2_anim_blender_import_export/releases/latest).

- **Blender 4.2+ (extension):** *Edit → Preferences → Get Extensions →
  Install from Disk*, point it at the zip.
- **Blender 3.6+ (legacy add-on):** *Edit → Preferences → Add-ons →
  Install*, point it at the zip, then tick "Total War RigidModel".

Then set **Texture Root Directory** in the add-on preferences to a folder
of extracted game textures, so materials come in with their images.

Building the zip yourself:

```
blender --command extension build --source-dir io_scene_rmv2
```

## Quick start

### A Warhammer-era unit

1. *File → Import → **Total War Animation (.anim)*** — pick the model's
   skeleton from `animations/skeletons/` (e.g. `humanoid01.anim`). This
   builds the armature.
2. *File → Import → **Total War RigidModel (.rigid_model_v2)*** — the
   meshes attach to that armature and their vertex groups get real bone
   names.
3. Import more `.anim` files onto the same armature for animations, each
   arriving as its own action.

The other order works too: import the model first, then the skeleton —
the importer renames the `bone_<i>` vertex groups and parents the meshes
retroactively, hidden LOD collections included. If you don't know which
skeleton a model wants, its name is on the root collection under
*Properties → Collection → RMV2*.

### A Shogun 2 unit part

Shogun 2's unit parts store **no model-space positions** — every vertex
lives in its bones' spaces — so the skeleton is not optional:

1. *File → Import → **Total War Animation (.anim)*** — import the model's
   **reference** skeleton from
   `animations/shogun_animation/.../reference/` (`man_shogun`, `horse`,
   `deer`, `bird`, `bear`, `whale`, `campaign_ship`).
2. Leave the armature selected.
3. *File → Import → **Total War Shogun 2 Unit Part
   (.variant_part_mesh)***.

Import it without an armature and you still get a mesh — the bind pose is
reconstructed from the file's own geometry — but it is approximate, bones
it can't reach are reported, and the importer warns you. **Export refuses
without an armature**, rather than writing something wrong.

Don't know which skeleton? Import the part anyway: the file names it, and
the importer tells you (`skeleton: man_shogun`), so you can load the right
one and re-import.

### An Empire or Napoleon unit

Empire's units are `.variant_weighted_mesh`, one file per unit per LOD,
holding every body part its variants can draw. Like Shogun 2's unit
parts they store **no model-space positions** — every vertex lives in
its bones' spaces — so the skeleton is required, not optional:

1. *File → Import → **Total War Animation (.anim)*** — import
   `animations/reference/tpose.anim`. Every unit model in both games is
   rigged to this same 41-bone skeleton, so there is nothing to guess.
2. Leave the armature selected.
3. *File → Import → **Total War Empire Unit Mesh
   (.variant_weighted_mesh)*** — pick `<unit>_lod1` … `<unit>_lod4`; you
   can select all four at once.

The `_lodN` suffix is understood, so the four files fill in **one** root
collection as LOD 0–3 (CA numbers from 1, Blender from 0) rather than
making four unrelated models. Import without an armature and you get a
clear error naming the file to load first — a mesh with no bind pose
would just collapse onto the origin.

Textures are not named inside the file; they are looked up by convention
as `unitmodels/textures/<unit>_diffuse.dds` (and `_normal`, `_gloss_map`)
under your Texture Root.

### An Empire destruction prop

`.rigid_model_animation` is self-contained — the
`.animatable_rigid_model` object list followed by a whole headerless
`.anim` — so there is nothing to import first. *File → Import → **Total
War Animated RigidModel (.rigid_model_animation)*** builds the armature
from the file, keys its animation as an action, and welds each object to
the bone it rides on.

### Editing and exporting back

Select the model's root collection (or just some meshes) and use the
matching *File → Export* entry. With an RMV2 root collection active, its
LOD sub-collections, version and skeleton name are filled in for you.

## Menu entries

Everything lives under *File → Import* and *File → Export*:

| Entry | Handles |
| --- | --- |
| Total War RigidModel (.rigid_model_v2) | Every RMV2 era, Shogun 2 included |
| Total War Shogun 2 RigidModel (.animatable_rigid_model, .rigid_model) | Both forms; the extension you pick decides which gets written |
| Total War Shogun 2 Unit Part (.variant_part_mesh) | Skinned parts, rigid equipment, and library files |
| Total War Empire Unit Mesh (.variant_weighted_mesh) | Empire/Napoleon skinned units, one file per LOD |
| Total War Animated RigidModel (.rigid_model_animation) | Objects and the animation that moves them, in one file |
| Total War Animation (.anim) | Skeletons and animations alike |

**One `.anim` entry does both jobs.** Skeleton files are just animations
whose two or three frames all repeat the bind pose, so the importer looks
at the file and the scene: no armature yet plus a bind-pose-looking file
builds the armature; otherwise the frames are keyed onto the existing
armature as an action.

### Import options

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

Multi-file selection works for RMV2, Shogun 2 RigidModel, Unit Part,
Unit Mesh and Animated RigidModel.

### Export options

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

## The scene it builds

```
model_name                 ← root collection: version, skeleton name, attachment points
├── model_name_lod0        ← LOD collection: camera distance, quality level
│   ├── body_lod0
│   └── head_lod0
├── model_name_lod1        ← hidden on import (eye icon, not excluded)
└── ...
```

All five formats build this same layout, so the panels and the `.anim`
importer work the same whichever one a model came from.

### Panels

- ***Properties → Object → RMV2 (RigidModel)*** — vertex format, material
  id, alpha mode, shader, render flag, texture directory and texture slot
  list, matrix index. **Copy RMV2 Settings to Selected** transfers the lot
  between objects.
- ***Properties → Collection → RMV2 (RigidModel)*** — on the root: file
  version, skeleton name, attachment points, auto-LOD override rows. On a
  LOD collection: level, camera distance, quality level.
- ***Properties → Object Data → RMV2 (.anim)*** (armatures) — skeleton
  name, `.anim` version, frame rate, flags. These become the export
  defaults.

**Object → Setup RMV2 LOD Collections** (also under **F3**) builds the
collection layout from a plain selection of meshes.

## Things that will trip you up

**"Nothing to export."** Make the model's collection active in the
outliner, or select the meshes. For `.variant_part_mesh` **libraries**,
use the collection — non-finest LODs import hidden, so a selection would
only catch LOD 0.

**Unit-part export refuses without an armature.** By design: the format
has nowhere to put a vertex except in a bone's space. Import the
reference `.anim` and attach the meshes. If the meshes really are
unskinned props, set their *Vertex Format* to **Variant Part Rigid** in
the Object panel.

**Vertex counts change.** Vertices are welded at the file's own
precision, and hard edges and UV seams split them — so counts can differ
from Blender's in both directions. The hard ceiling is **65,536 vertices
per mesh** (16-bit indices); past that, split the object.

**Skinned export needs resolvable bone indices.** Keep the vertex group
names the importer made (`bone_12`-style, or real bone names after a
`.anim` import), or use an armature whose bone order matches the game
skeleton. Bone *order* is what the game cares about — armatures this
add-on creates stamp each bone with an `rmv2_bone_index` custom property,
so renaming and reordering bones in Blender is safe.

**Textures don't show up.** Set the **Texture Root Directory** in the
add-on preferences to your extracted-texture folder. The file stores
paths, not images.

**Scale must match.** If you import a model at a non-default scale,
import its `.anim` at the same one.

**Moving an object moves the file's pivot.** RMV2 vertices are stored
relative to a pivot the game adds back at render time, so the importer
puts the pivot at the object's origin. On export the object's world
*translation* becomes the pivot; rotation and scale are baked into the
vertices. (Shogun 2 has no pivot field — its meshes live in bone space —
so there the translation goes into the vertices too.)

**"This file is for skeleton X."** The armature remembers which skeleton
it was built from and refuses an animation meant for a different one, so
you find out immediately rather than after the pose comes out mangled.

**A model gets exactly one armature.** Importing a second skeleton onto a
model that already has one is an error, not a silent second rig. To rig a
*different* model with the same skeleton, select that model's meshes or
collection first.

**Shogun 2 has no separate skeleton files.** Every `.anim` carries the
full bone table, and models name the `.anim` they belong to rather than
the other way round.

**Empire unit meshes need `tpose.anim` first.** `.variant_weighted_mesh`
does not even name its skeleton — it does not have to, since every unit
in Empire and Napoleon uses the same 41-bone rig at
`animations/reference/tpose.anim`. Import that, leave it selected, then
import the mesh.

**One `.variant_weighted_mesh` is one LOD.** The ladder is four separate
files, so a full export is four exports with *LOD Level* set to 0, 1, 2,
3 — writing files named `<unit>_lod1` … `<unit>_lod4`, the way CA numbers
them. On import the suffix is read back and the four files share a single
root collection.

## Format notes

### Coordinates

The game is Y-up and effectively **left-handed** — AssetEditor mirrors its
entire viewport with a `Scale(-1,1,1)` projection to match what the game
draws. Blender is Z-up right-handed, so the conversion carries exactly one
reflection:

```
blender = (-x, -z, y)
```

Triangle winding is reversed to compensate and the UV V axis is flipped.
The upshot: what you see in Blender matches the in-game orientation, not
a mirror image of it. Export reverses all of it.

### Materials

Textures are resolved against the texture root and wired into a Principled
BSDF: BaseColour/Diffuse, Normal, Gloss, Specular. Two CA-specific packed
textures get real node graphs instead of being dropped:

- **MaterialMap** — R = metallic, G = roughness. Takes priority over Gloss
  when both are present.
- **Mask** — RGB channels blend three adjustable "player colour" swatches
  over the base colour (faction tinting); alpha drives emission strength.

### Shogun 2

Shogun 2 predates everything else here and is absent from
[TheAssetEditor](https://github.com/donkeyProgramming/TheAssetEditor)'s C#
reference, so its layouts were reverse-engineered from the game's packs.
What differs:

- **No per-vertex skinning in the rigid model formats.** A mesh is welded
  to exactly one bone — the material's bone index — and stored in that
  bone's space. Import binds each mesh to its bone with a Child Of
  constraint; export writes the vertices back in bone space.
  `.variant_part_mesh` is the exception.
- **`.rigid_model_v2` v1/v2** put the skeleton name in a 512-byte UTF-16
  field *after* the LOD table, use a 48-byte mesh header, and describe
  materials as runs of fixed-width fields whose composition depends on the
  material id.
- **`.anim` v1** uses UTF-16 bone names with a character count and
  uncompressed float32 quaternions, and carries its animation events
  (`FIRE_TIME`, `FIRE_POSITION`, …) inside the file — those ride on the
  armature and are written back. A minority of files have no version field
  at all and ten floats per bone per frame; they're detected
  automatically, and export as *Anim v0*.
- **`.animatable_rigid_model`** is a flat list of bone-welded objects
  (jointed props — siege engines, ballistae) with no LOD tree of its own;
  the LOD ladder is separate `..._lod1` / `..._lod2` files.
- **`.rigid_model`** is the very same container *minus* the per-object
  bone index — that is the whole of what "animatable" adds — closing
  instead with a bounding box. Objects are matched to their form by
  looking ahead in the file rather than by trusting its name.

### `.variant_part_mesh`

The format Shogun 2's actual units are built from: one file per helmet,
torso, mask or saddle, assembled at runtime by the variant mesh
definitions. It is in neither TheAssetEditor nor RPFM.

In Blender it behaves like any skinned mesh — vertex groups plus an
armature modifier — because that's what it is: up to **two weighted bone
influences per vertex**, effectively CA's later *Weighted2*. Bone indices
address the reference skeleton directly, with no intermediate palette.

What has no counterpart elsewhere is the storage: each vertex holds its
position **once per influence, each in that influence's own bone space**,
with one byte giving the first influence's weight. Single-influence
vertices leave the second copy zeroed, which is how the two cases are
told apart. There is no model-space copy anywhere in the file — hence the
armature requirement above.

The fallback bind pose, used when no armature is available, works because
every two-influence vertex is one sample of the rigid transform between
two bone spaces: fit those, span the resulting graph, and the pose falls
out of the file alone.

**A second, rigid vertex layout** — plain float32 model-space positions,
no skinning — covers equipment, crests and blank parts, and round-trips
bit-exactly since nothing is quantized. Which layout gets written comes
from each mesh's own *Vertex Format* setting in the Object panel, stamped
at import, exactly as the `.rigid_model_v2` path works. Rigid vertices
also carry a second UV set (ambient occlusion), imported as `UVMap_1`
when non-zero.

One known gap: export always writes container version 3. Files that came
in as v0 or v2 re-export as v3, and the material parameter block, the
`crests` attachment and the non-default material names are not yet
carried through. Geometry, names, attachment slots and skinning are.

**Library files.** A few `variantmodels/equipment/` files pack dozens of
unrelated props into one container — `mesh1.variant_part_mesh` holds 52
props (Geisha dagger, Long yari, …) across 142 parts, each with its own
name, LOD ladder and attachment slot. These round-trip in full: import
gives one object per part, named `<prop>_lod<N>`, and export writes back
every prop's name, slot, material names and original ordering.

LOD numbers in those names are whatever the artist used — CA's ladders
usually start at `lod1`, but `rigid_equip_Yumi` starts at `lod2` and two
parts in `mesh1` have no suffix at all — so each prop's levels are
numbered from its own finest rather than from a file-wide floor.

The only thing that doesn't survive is geometry that never rendered. 19 of
315 named parts carry vertices no triangle references (151 in total); each
sits at exactly the position of a vertex the mesh *does* use and differs
only in normal, tangent or UV — split vertices left at a seam. 12 parts
also carry zero-area triangles referencing them (46, across `mesh2`'s
Boshin rifles). Blender stores neither, so both are dropped and the
importer says so. Nothing that was ever drawn is lost.

### Empire and Napoleon

The two older games share most of the Shogun 2 machinery — the same
`.rigid_model` container, and `.anim` files that are all the headerless
variant — plus two formats of their own.

**`.variant_weighted_mesh`** is what their units are actually built
from: one file per unit per LOD, holding every body part its variants can
draw (`<unit>_head01`, `<unit>_body02`, `<unit>_legs01` …). Shogun 2
ships exactly one such file, which is why it went unread until now;
Empire ships 1269 and Napoleon 286. It is in neither TheAssetEditor nor
RPFM.

Its skinning is the same idea as `.variant_part_mesh`, generalised:

    f32[2]   uv
    f32[3]   tangent    ) in the first influence's bone space
    f32[3]   binormal   )
    u32      influence count, 1 to 8
    per influence:
        u32     bone index
        f32[3]  position, in that bone's space
        f32[3]  normal, in that bone's space
        f32     weight
    f32[4]  colour RGBA

So again **no model-space position exists anywhere in the file**, and
again the armature is required. Unlike VMPF the influence count varies
per vertex, which makes the stride variable — that, rather than the field
layout, is the format's one real difficulty. Everything is plain float32,
so nothing is quantized and geometry round-trips exactly.

The tangent and binormal were identified by rebuilding the tangent frame
from each triangle's bone-local positions and UVs and correlating: the
stored vectors line up with the UV-derived tangent and binormal
respectively (mean dot +0.87 and +0.90, cross terms ~0.00), which also
places them in bone space rather than model space.

That closing colour is zero in all 1305 vanilla files, so it cannot be
read off directly, and it was long treated here as reserved padding.
Three things place it: it is per *vertex* rather than per influence
(45% of vanilla vertices carry two influences, and a flat 16 bytes per
vertex is what parses every file to the byte); it sits immediately after
the tangent frame, exactly where a rigid-model vertex keeps its RGBA
colour, and is exactly its size; and CA's older `testdata` files — the
same container from before the colour channel existed — do not have it,
nor do the rigid objects in their attachment section. So it is the same
channel the rigid model gained going from 14 to 18 floats. No vanilla
weighted mesh ever writes a value into it, so the name is informed by
position and provenance rather than by a file that uses it, and whatever
is read is kept either way.

**Attachments.** After the last part comes a second section: the props a
unit hangs off a single bone. Each entry is a name and a bone index in
front of an ordinary `.animatable_rigid_model` object, so the same reader
handles both. It is a lone zero word in all but six vanilla files, which
is why it too read as padding at first — but
`unitmodels/euro_equipment.variant_weighted_mesh` (in both games) is 134
of them and *no* skinned parts at all: every musket, spear and flagpole
in the game. Those import as rigid objects bound to their bone with a
Child Of constraint, the way `.animatable_rigid_model` objects do; on the
way out, an object with no vertex groups goes back into this section
rather than becoming a skinned part.

**Older layouts.** CA's `testdata` folders keep three earlier generations
of the container, each dropping one thing from the shipping layout:

| part table | vertex head | colour block | files |
| --- | --- | --- | --- |
| nameless | uv | no | 2 |
| nameless | uv + tangent frame | no | 20 |
| named | uv + tangent frame | no | 4 |
| named | uv + tangent frame | yes | everything that shipped |

The nameless table is a three-word header — part count, total vertices,
total indices — with no names anywhere, and those files also skin more
heavily than anything that shipped, up to 11 influences on a vertex where
shipping files stop at 8. Which layout a file uses is decided by trying
each and keeping the one that consumes the file exactly, the same trick
the rigid-model reader uses for its bone indices.

There are no texture slots — the part name doubles as the texture-set
name, and the variant system picks the images at runtime, so materials
are built from `unitmodels/textures/<unit>_diffuse.dds` by convention.
A headerless variant (12 files) drops the magic, version and parameter
blocks and opens straight at the part table; it gets synthetic version 0,
the same trick `anim_format` uses for the headerless `.anim`.

**Older object versions.** The rigid-model container has been around
since before Empire, and its object shrinks as you go back. Everything
below `uv2` sits at the same offset in all of them, so one reader covers
the lot:

| Object version | Texture slots | Params | Vertex |
| --- | --- | --- | --- |
| 5, 4 | 4 (first 3 flagged) | yes | 20 floats |
| 3 | 4 (first 3 flagged) | no | 20 floats |
| **2** | **3, all flagged** | no | **18 floats** — no `uv2` |
| **1** | **1, unflagged** | no | **18 floats** — no `uv2` |
| **0 (headerless)** | **1, unflagged** | no | **14 floats** — no `uv2`, no colour |

Each step adds exactly one thing to the one before it, which is why a
single reader covers the lot.

Version 1 is the Empire/Napoleon object: 23 files across the two games
(12 distinct — Napoleon ships the same set bar the naval cannon) — the
campaign mountains, the flagpole, the boarding plank and that cannon. Version 0 goes further and drops the per-object magic and version
words entirely, so the file's object count runs straight into the first
name; it is detected by the *absence* of the magic where the first object
should begin, and gets a synthetic version 0, the same trick used for the
headerless `.anim` and `.variant_weighted_mesh`. One shipping file uses
it (`enginemodels/cannon_test_model`).

Version 2 sits between 1 and 3, and only CA's `testdata/fence` models use
it. All of them keep bone indices in the animatable form.

Losing the magic costs the headerless reader its landmark for the
bone-index lookahead, so for those files whether the objects carry one is
settled per *file* rather than per object: both readings are tried and
the one that consumes the file exactly is kept. Vanilla has examples
either way — `cannon_test_model` has bone indices, `testdata`'s `loki`
and `victory_test` (18 and 17 objects) do not.

**`.rigid_model_animation`** is not a new container at all: it is the
`.animatable_rigid_model` object list followed by a whole headerless
`.anim`, used for self-contained animated props — the destruction
sequences for cannon carriages, ships and campaign buildings. All 725
Empire and 784 Napoleon files round-trip with no new format code, via
`arm_format.load(data, allow_trailing=True)`. Since the skeleton travels
inside the file, there is nothing to import first and nothing to guess.

### Deliberately unsupported

Refused with a clear error rather than misparsed:

- **`.rigid_model_v2` v0** — 4 vanilla files, all `castle_01_gate_piece09*`.
  No skeleton-name field, and meshes beyond what its LOD table declares.
- **`.anim` v4** — referenced by AssetEditor's version enum, but it does
  not occur in Empire's or Napoleon's packs: both ship the headerless
  variant exclusively (3758 and 4027 files), so there is still nothing
  to work from.
- **Empire's Elite Units DLC unit meshes** — 224
  `.variant_weighted_mesh` files shipped under a per-file cipher. Their
  Napoleon equivalents are in the clear and work normally.

A handful of `testdata/` `.anim` oddities are refused too; that folder is
CA's scratch space, holding exports from formats that never shipped. Its
weighted meshes and rigid models do read, in every generation CA left
behind.

## Development

```
python tests/test_format.py                     # format layer, no Blender
blender --background --factory-startup --python tests/test_blender_roundtrip.py
```

The format layer (`rmv2_format.py`, `anim_format.py`, `arm_format.py`,
`vmpf_format.py`, `vwm_format.py`) is bpy-free and usable standalone. Everything the
Blender side shares lives in `utils.py` (coordinate conversion),
`skeleton.py` (bone indices and bind poses), `scene_layout.py` (the
root/LOD collection tree), `mesh_build.py` (geometry → Blender mesh) and
`export_rmv2.extract_mesh_arrays` / `weld_loops` (the shared export path),
so the formats stay consistent instead of each growing its own copy.
`.rigid_model_animation` needs no format module at all: it is
`arm_format.load(data, allow_trailing=True)` followed by
`anim_format.load(arm.trailing)`.

```python
from io_scene_rmv2 import rmv2_format as rf
rmv = rf.load(open("unit.rigid_model_v2", "rb").read())
print(rmv.version, rmv.skeleton_name,
      [(len(l.models), l.camera_distance) for l in rmv.lods])
open("out.rigid_model_v2", "wb").write(rf.save(rmv))

from io_scene_rmv2 import anim_format as af
anim = af.load(open("hu1_sword_attack_01.anim", "rb").read())
print(anim.version, anim.skeleton_name, anim.frame_rate,
      anim.frame_count, [b.name for b in anim.bones][:5])
tracks = af.resolve(anim)      # dense (frames, bones, 3/4) arrays
```

Binary layout for the RMV2-era formats follows the C# reference in
[TheAssetEditor](https://github.com/donkeyProgramming/TheAssetEditor)
(`Shared/GameFiles/RigidModel`, `Shared/GameFiles/Animation`)
byte-for-byte.
