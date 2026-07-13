# Total War RigidModel (.rigid_model_v2) + Animation (.anim) — Blender Import/Export

A Blender add-on for Creative Assembly's **RMV2** mesh format and **.anim**
skeleton/animation format used by the Total War games (Rome 2 → Warhammer 3
era). Binary layout follows the C# reference implementation in
[TheAssetEditor](https://github.com/donkeyProgramming/TheAssetEditor)
(`Shared/GameFiles/RigidModel`, `Shared/GameFiles/Animation`)
byte-for-byte.

Tested on **Blender 5.0**; written to be compatible with 3.6+ (guards for
the 4.1 normals API changes are in place).

## Features

**Import** (RMV2 v5–v8)

- By default only the most detailed LOD is imported; the *Import All
  LODs* option brings in every LOD as its own collection with camera
  distance / quality level / LOD level preserved (only LOD0's collection
  is left visible — the rest are hidden via the outliner eye icon, not
  excluded).
- Vertex formats: Static, Weighted (2 bone influences), Cinematic
  (4 influences); read-only support for Collision, Position16,
  CustomTerrain 1/2.
- Positions (incl. CA's half-float `W`-scale trick), custom split normals,
  both UV channels (static meshes), vertex colours (v8 / static), bone
  weights.
- Vertex groups get standard `bone_<i>` names (renamed to real bone names
  by a later `.anim` import), or the bones' names directly when an
  armature is selected during import. Attachment points are stored as an
  editable list on the root collection.
- Mesh pivot becomes the object origin.
- Blender materials built from the file's texture list; textures resolved
  against a configurable **texture root** folder (add-on preferences).
  BaseColour/Diffuse, Normal, Gloss and Specular are wired into a
  Principled BSDF. Two CA-specific packed textures get real node graphs
  instead of being dropped: **MaterialMap** (R=metallic, G=roughness,
  takes priority over Gloss when both are present) and **Mask**
  (RGB channels blend 3 adjustable "player colour" swatches over the base
  colour — faction tinting — while alpha drives emission strength).
- Everything else round-trips through object/collection settings:
  material id, alpha mode, render flags, shader name + raw shader bytes,
  texture directory, matrix indices, string/float/int/vec4 material
  parameters, transform matrices, attachment points, even the
  undocumented padding bytes.
- Multi-file import, optional attachment-point empties, global scale.

**Export** (RMV2 v6–v8)

- LODs from collections; a flat selection exports as a single LOD, or
  enable **Generate LODs (Decimate)** to auto-build the lower LODs with
  progressively stronger collapse-decimation (each LOD halves the
  triangle budget: 100% / 50% / 25% / 12.5%).
- Applies modifiers (armature deform automatically excluded so the rest
  pose is written), triangulates, computes the full tangent basis
  (Blender's mikktspace tangents; manual fallback for ngons).
- Vertices welded to the file's actual quantized precision, 16-bit index
  buffer, per-mesh bounding boxes, correct section offsets — the writer
  re-parses its own output as a sanity check before writing to disk.
- Bone weights: top-2/top-4 influences, normalized, byte-quantized so they
  sum to exactly 255. Vertex groups resolve to bone indices via the
  stored attachment points → `bone_<i>` names → armature bone order
  (with `bn_` prefix handling).
- `AUTO` vertex format (static vs cinematic) and `AUTO` material id
  (default_type vs weighted).
- Optional high-precision positions (vectorised brute-force of the
  half-float `W` mantissa, same algorithm AssetEditor uses).
- Alpha mode, all texture slots, attachment points, and every preserved
  raw field written back.

**Animation import** (.anim v5–v8)

- Skeleton files (`animations/skeletons/*.anim`) build a real armature:
  bone hierarchy, bind pose as the rest pose, original bone indices
  stamped as `rmv2_bone_index` custom properties. (Skeleton files are
  just short animations whose 2–3 frames all repeat the bind pose — the
  two menu entries are intent, not file detection.)
- Animation files become actions (location + quaternion F-curves per
  bone). Static-frame tracks (v7) and the v8 bit-packed encodings
  (full-float / ranged byte compression) are decoded like AssetEditor
  does. Bones the file doesn't animate keep the rest pose, matching how
  the game composes animations over the bind pose.
- Scene FPS and frame range are set from the file header.
- Separate **Skeleton** and **Animation** menu entries: skeletons build
  the model's one armature (error if it already has one), animations
  require it (error if it doesn't). Works in **either order** with mesh
  imports (see Usage).

**Animation export** (.anim v5–v7)

- *Animation* mode samples the evaluated pose (constraints included) over
  the scene frame range; *Bind Pose* mode writes the rest pose as a
  skeleton file (two identical frames, matching the vanilla layout).
- Skeleton name / frame rate / v7 flag strings default to what was stored
  on the armature at import time.

## Installation

**As an extension (Blender 4.2+):** zip the `io_scene_rmv2` folder and use
*Edit → Preferences → Get Extensions → Install from Disk*, or:

```
blender --command extension build --source-dir io_scene_rmv2
```

**As a legacy add-on (3.6+):** zip `io_scene_rmv2` and use
*Edit → Preferences → Add-ons → Install*, then enable
"Total War RigidModel".

Set the **Texture Root Directory** in the add-on preferences to a folder
with extracted game textures (RPFM / AssetEditor dumps) if you want
materials to show images on import.

## Usage

### Import

*File → Import → Total War RigidModel (.rigid_model_v2)*. Options:

| Option | Meaning |
| --- | --- |
| Import All LODs | Off (default): only the most detailed LOD. On: every LOD in its own collection |
| Build Materials | Create Principled BSDF node trees with the file's textures |
| Texture Root | Override the preferences folder for texture lookup |
| Attachment Point Empties | Create empties for the attachment points |
| Attach To Selected Armature | Name vertex groups after a selected armature's bones and parent the meshes to it |
| Scale | Uniform import scale |

The importer creates:

```
model_name                 ← root collection (version, skeleton, attach points)
├── model_name_lod0        ← LOD collection (camera distance, quality…)
│   ├── body_lod0
│   └── head_lod0
├── model_name_lod1
└── ...
```

### Export

Select the root collection (or just select some meshes) and use
*File → Export → Total War RigidModel (.rigid_model_v2)*.

- With an RMV2 root collection active, its LOD sub-collections, version and
  skeleton name are used automatically.
- With a flat selection (a single LOD's worth of meshes), the file gets
  one LOD — or turn on **Generate LODs (Decimate)** to auto-create the
  lower LODs by decimation.
- Skinned meshes need resolvable bone indices — keep the vertex group
  names the importer created (`bone_12`-style or bone names from a
  `.anim` import), or parent/modify with an armature whose bone order
  matches the game skeleton.

### Animations / skeletons

Two import menu entries keep the order honest — a model gets exactly
**one** armature, created from the bind-pose skeleton file:

- *File → Import → **Total War Skeleton** (.anim)* — builds the armature.
  Pick the model's skeleton file from `animations/skeletons/` (e.g.
  `humanoid01.anim` — the name is shown in the root collection's RMV2
  settings). The armature is linked into the model's root collection, the
  meshes' `bone_<i>` vertex groups are renamed to the real bone names and
  the meshes are parented — hidden LOD collections included. Errors out
  if the model already has an armature.
- *File → Import → **Total War Animation** (.anim)* — keys an animation
  onto the model's existing armature as an action. Errors out if there is
  no armature yet (import the skeleton first).

Skeleton and animation files are structurally identical (skeletons are a
few identical bind-pose frames), so the add-on cannot tell them apart —
the two entries express what *you* mean to do.

Both entries figure out "the model" from whatever is handy: selected
meshes, the selected armature, or the active RMV2 collection. Repeat the
animation import to swap in different animations.

Skeleton-then-model also works: import the skeleton into an empty scene,
then import a `.rigid_model_v2` — the vertex groups are named after the
armature's bones directly and the meshes are attached.

*File → Export → Total War Animation (.anim)* exports the selected
armature: **Animation** samples the scene frame range, **Bind Pose
(Skeleton)** writes the rest pose. Version 7 is what AssetEditor and the
WH-era modding pipelines expect.

### Per-mesh settings

*Properties → Object → RMV2 (RigidModel)*: vertex format, material id,
alpha mode, shader, render flag, texture directory + texture slot list,
matrix indices. (Rare raw fields — extra transform matrices, padding
bytes, shader parameter lists — are preserved invisibly for byte-perfect
re-export.) *Copy RMV2 Settings to Selected* transfers them between
objects.

*Properties → Collection → RMV2 (RigidModel)*: file version/skeleton and
the attachment point list (name, bone index, 3×4 rest matrix) on the
root, camera distance / quality / level on each LOD.

Helper: **F3 → "Setup RMV2 LOD Collections"** builds the collection layout
from selected meshes.

## Conventions & gotchas

- Coordinates: game is Y-up right-handed, Blender Z-up — the add-on
  converts (`blender = (x, -z, y)`), flips triangle winding
  (DirectX CW ↔ Blender CCW) and flips the UV V axis. What you see in
  Blender is oriented correctly; export reverses all of it.
- Vertices are welded per unique position/normal/tangent/UV/colour at
  *file* precision; hard edges and UV seams split vertices, so counts can
  differ from Blender's — max 65 536 unique vertices per mesh (16-bit
  indices).
- The skeleton itself is **not** in RMV2 files (they only reference a
  skeleton name, e.g. `humanoid01`); armatures come from the matching
  `.anim` skeleton file (`animations/skeletons/<name>.anim` in the game
  packs).
- Bone order matters to the game. Armatures created by this add-on stamp
  every bone with a `rmv2_bone_index` custom property; exports use it, so
  renaming or reordering bones in Blender is safe.
- `.anim` version 8 (Warhammer 3) is import-only — same as AssetEditor.
  Export as v7. Multi-part v8 files import their first part only.
  Version 4 (pre-Rome 2) is not supported (different string encoding and
  body layout; AssetEditor cannot read those either).
- Terrain and collision vertex formats are import-only, matching
  AssetEditor.
- Re-saving an imported file without modifications is byte-identical
  (original vertex blocks and bounding boxes are passed through; for
  `.anim` this holds for v5–v7).

## Development

```
python tests/test_format.py                     # format layer, no Blender
blender --background --factory-startup --python tests/test_blender_roundtrip.py
```

`io_scene_rmv2/rmv2_format.py` and `io_scene_rmv2/anim_format.py` are
bpy-free and usable standalone for batch-processing game files:

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
