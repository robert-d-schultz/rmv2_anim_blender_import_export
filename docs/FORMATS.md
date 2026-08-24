# Format notes

How Creative Assembly's formats are laid out, and the parts of them that
are not obvious. For what is supported, see the
[README](../README.md); for how well it round-trips, see
[CORPUS.md](CORPUS.md).

The RMV2-era layouts follow the C# reference in
[TheAssetEditor](https://github.com/donkeyProgramming/TheAssetEditor)
(`Shared/GameFiles/RigidModel`, `Shared/GameFiles/Animation`). Everything
before Rome 2 is in neither that nor RPFM and was reverse-engineered from
the games' packs.

## Using the format layer on its own

`rmv2_format.py`, `anim_format.py`, `arm_format.py`, `vmpf_format.py` and
`vwm_format.py` are bpy-free, so they work as a plain Python library:

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

`.rigid_model_animation` needs no module of its own: it is
`arm_format.load(data, allow_trailing=True)` followed by
`anim_format.load(arm.trailing)`.

The Blender side keeps its shared pieces in `utils.py` (coordinate
conversion), `skeleton.py` (bone indices and bind poses),
`scene_layout.py` (the root/LOD collection tree), `mesh_build.py`
(geometry to Blender mesh) and `export_rmv2.extract_mesh_arrays` /
`weld_loops` (the shared export path), so the formats stay consistent
instead of each growing its own copy.

## Coordinates

The game is Y-up and effectively **left-handed** — AssetEditor mirrors
its entire viewport with a `Scale(-1,1,1)` projection to match what the
game draws. Blender is Z-up right-handed, so the conversion carries
exactly one reflection:

```
blender = (-x, -z, y)
```

Triangle winding is reversed to compensate and the UV V axis is flipped.
The upshot is that what you see in Blender matches the in-game
orientation rather than a mirror image of it. Export reverses all of it.

**Vertices are pivot-relative.** RMV2 stores positions relative to a
pivot the game adds back at render time, so the importer puts the pivot
at the object's origin. On export the object's world *translation*
becomes the pivot; rotation and scale are baked into the vertices.
Shogun 2 has no pivot field — its meshes live in bone space — so there
the translation goes into the vertices too.

## Materials and textures

Textures are resolved against the texture root and wired into a
Principled BSDF: BaseColour/Diffuse, Normal, Gloss, Specular. Two
CA-specific packed textures get real node graphs instead of being
dropped:

- **MaterialMap** — R = metallic, G = roughness. Takes priority over
  Gloss when both are present.
- **Mask** — RGB channels blend three adjustable "player colour"
  swatches over the base colour (faction tinting); alpha drives emission
  strength.

Normal maps are CA's "orange" packing (R = 1, G = Y, B = 0, A = X, with
Z reconstructed), decoded through a node group rather than fed to
Blender's normal map node raw.

### Short material headers

The weighted material is the long one. Every era also has a set of
materials with short fixed headers of their own, and anything read with
the weighted layout runs off the end of a much shorter header. Dispatch
goes by material id **and** expected header size, because the ids move
between eras. Decoded so far:

- **Terrain tiles** — id 66 or 97 in the Rome 2 era, 101 in Warhammer,
  carrying five trailing words or six depending on which.
- **The projected-decal family** — 67, then 87, then 95: a texture path
  followed by one, nine or ten floats.
- **Materials with no header at all** — bow waves and one of the
  terrain-tile ids go straight from the common header to the vertices,
  so the vertex layout has to come from the stride.
- **Cloth, rope and collision shapes** — an ordinary weighted material
  followed by a block of their own. That block is simulation data
  (constraint pairs and rest lengths) and nothing in Blender could
  rebuild it, so it is kept exactly as read and written back with the
  material. The same goes for the extra indices Attila's ropes put after
  their index block.
- **Interface banners** — ids 29 and 30 at 288 bytes: a 256-byte model
  name and eight words, zero in all 19 vanilla examples. The material
  declares no vertex format at all, so the layout comes from the stride.

Fixed-width string fields keep whatever was in memory **after** their
terminator. Zero-padding them on write is the obvious thing to do and it
breaks byte-identity: 163 terrain tiles and 33 trees stopped re-saving
correctly the moment those fields were decoded properly.

## Vertex layouts

### Warhammer's sway vertex

Vertex format **12**, stride 20, under 255 Warhammer models — hanging
cloth, bone cages, leaf cards, a tree's canopy. It is two half4s whose W
components carry the UV between them (position + U, normal + V), plus a
u8x4 colour whose **alpha is the wind-sway weight**.

Unlike every other half position in the format the XYZ is *not* scaled by
W, because W is the U coordinate. What settles it: adding the material's
pivot to the raw half3 lands exactly on the header's own bounding box.

Everything it stores lands on an ordinary Blender mesh field, so how far
a leaf or a hanging cloth moves in the wind is something you can paint.

### Tree billboards put their indices first

A tree's furthest LOD is a **generated billboard** — a flat card in the
plain 12-byte position-and-uv layout — and whatever writes it is not what
writes the rest of the file. Its mesh section runs the other way round:
material, then the **index block**, then the vertices.

Reading `index_offset` and `vertex_offset` in the usual order therefore
counts the index block as part of the material, making it look 36 to 60
bytes longer than it is — which is why it read for two sessions as a
material of unknown size. Its `mesh_section_size` then stops at the
vertex block rather than covering it, so the vertices run past the end of
their own section. That works only because such a mesh is always the last
one in the file, as all 629 in Warhammer 3 are, and it means the size can
be written back but not recomputed.

Detection is deliberately narrow (`RmvModel.indices_first`):
`index_offset` below `vertex_offset` with the indices exactly filling the
gap. A file that is merely odd still fails loudly rather than being read
on a guess.

### Channels with no Blender slot

Some layouts carry per-vertex data no ordinary mesh has a field for: a
vegetation vertex stores the rest position its branch sways from and
eight wind weights, a bow wave stores a second position for where the
crest travels to, custom terrain has two spare colour channels. Those
ride through Blender as **point attributes** — `rmv2_pivot`,
`rmv2_wind_0`, `rmv2_pos2` and so on — visible in the spreadsheet,
editable, and read back on export.

The eight wind weights are halves of unknown meaning, and five of the
eight are constant per mesh in every vanilla file looked at.

## `.anim`

### Version 8 and float64

Version 8's byte-packed channels decode as `base + (byte / 127) × scale`,
and Warhammer 3 has bones whose base dwarfs their scale — 0.7071 against
4.2e-06, a byte step of 3.3e-08 where float32 resolves 6e-08. Decoded in
float32, adjacent bytes collapse onto one value before they ever reach an
array, and about 1.6% of files cannot be written back as they were read.

Decoding those channels at **float64** fixes it completely. v8 frames are
the one layout held at that width, since every other quantization here
(int16 / 32767) fits float32 exactly.

### Fields that were assumed constant

- The u32 after the version is **not** the 1 AssetEditor takes it for.
  Across Warhammer 3's 34,984 animations 51 carry 2 and 41 carry 0;
  Three Kingdoms uses all three values freely. In Warhammer, 291 of 6975
  files carry 0, and nearly all of them are the animations that ride on
  a rigid model — buildings, chariots, war machines — rather than on a
  character rig. That is the closest that field has come to a meaning.
- Quaternions were clipped to ±1.0 *before* being scaled to int16, which
  turned a stored -32768 into -32767. Clamping the scaled value instead
  keeps the extreme and still guards the overflow.
- A part holding its pose entirely in the static frame follows it with
  `duration × fps + 1`, not the 3 it usually reads as.

### Multi-part v8

v8 parts play back to back, per AssetEditor's `AnimationClip`. The
importer concatenates them into one timeline, so a re-export writes a
single part: it plays the same, but differs structurally from the
original.

## Rome 2 is two eras in one game

Rome 2 shipped with **RMV2 v5** and **`.anim` v4**, both of which write
every fixed-width string as UTF-16 at twice the width, then switched
mid-life to v6 and v5, which are the same layouts in UTF-8. Version 4
goes further back still: it keeps frames the way Shogun 2 does, every
bone in every frame as float32, with two per-bone bitfields where v5
keeps its mapping tables.

**RMV2 v3**, the version Rome 2's 3D interface models use, is not a Rome
2 layout at all — it is Shogun 2's, carried forward one release.

The `.anim` event block turns out not to be a Shogun 2 exclusive either:
59 Rome 2 cutscene animations close with one, always empty.

## Shogun 2

Shogun 2 predates everything else here and is absent from TheAssetEditor,
so its layouts were reverse-engineered from the game's packs. What
differs:

- **No per-vertex skinning in the rigid model formats.** A mesh is welded
  to exactly one bone — the material's bone index — and stored in that
  bone's space. Import binds each mesh to its bone with a Child Of
  constraint; export writes the vertices back in bone space.
  `.variant_part_mesh` is the exception.
- **`.rigid_model_v2` v1/v2** put the skeleton name in a 512-byte UTF-16
  field *after* the LOD table, use a 48-byte mesh header, and describe
  materials as runs of fixed-width fields whose composition depends on
  the material id.
- **`.anim` v1** uses UTF-16 bone names with a character count and
  uncompressed float32 quaternions, and carries its animation events
  (`FIRE_TIME`, `FIRE_POSITION`, …) inside the file — those ride on the
  armature and are written back. A minority of files have no version
  field at all and ten floats per bone per frame; they are detected
  automatically and export as *Anim v0*.
- **`.animatable_rigid_model`** is a flat list of bone-welded objects
  (jointed props — siege engines, ballistae) with no LOD tree of its own;
  the LOD ladder is separate `..._lod1` / `..._lod2` files.
- **`.rigid_model`** is the very same container *minus* the per-object
  bone index — that is the whole of what "animatable" adds — closing
  instead with a bounding box. Objects are matched to their form by
  looking ahead in the file rather than by trusting its name.
- **No separate skeleton files.** Every `.anim` carries the full bone
  table, and models name the `.anim` they belong to rather than the other
  way round.

### `.variant_part_mesh`

The format Shogun 2's actual units are built from: one file per helmet,
torso, mask or saddle, assembled at runtime by the variant mesh
definitions. It is in neither TheAssetEditor nor RPFM.

In Blender it behaves like any skinned mesh — vertex groups plus an
armature modifier — because that is what it is: up to **two weighted bone
influences per vertex**, effectively CA's later *Weighted2*. Bone indices
address the reference skeleton directly, with no intermediate palette.

What has no counterpart elsewhere is the storage: each vertex holds its
position **once per influence, each in that influence's own bone space**,
with one byte giving the first influence's weight. Single-influence
vertices leave the second copy zeroed, which is how the two cases are
told apart. There is no model-space copy anywhere in the file — hence the
armature requirement on import and export.

The fallback bind pose, used when no armature is available, works because
every two-influence vertex is one sample of the rigid transform between
two bone spaces: fit those, span the resulting graph, and the pose falls
out of the file alone.

**A second, rigid vertex layout** — plain float32 model-space positions,
no skinning — covers equipment, crests and blank parts, and round-trips
bit-exactly since nothing is quantized. Which layout gets written comes
from each mesh's own *Vertex Format* setting in the Object panel, stamped
at import. Rigid vertices also carry a second UV set (ambient occlusion),
imported as `UVMap_1` when non-zero.

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

The only thing that does not survive is geometry that never rendered. 19
of 315 named parts carry vertices no triangle references (151 in total);
each sits at exactly the position of a vertex the mesh *does* use and
differs only in normal, tangent or UV — split vertices left at a seam. 12
parts also carry zero-area triangles referencing them (46, across
`mesh2`'s Boshin rifles). Blender stores neither, so both are dropped and
the importer says so. Nothing that was ever drawn is lost.

## Empire and Napoleon

The two older games share most of the Shogun 2 machinery — the same
`.rigid_model` container, and `.anim` files that are all the headerless
variant — plus two formats of their own.

### `.variant_weighted_mesh`

What their units are actually built from: one file per unit per LOD,
holding every body part its variants can draw (`<unit>_head01`,
`<unit>_body02`, `<unit>_legs01` …). Shogun 2 ships exactly one such
file, which is why it went unread for so long; Empire ships 1269 and
Napoleon 286. It is in neither TheAssetEditor nor RPFM.

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
again the armature is required. Unlike `.variant_part_mesh` the influence
count varies per vertex, which makes the stride variable — that, rather
than the field layout, is the format's one real difficulty. Everything is
plain float32, so nothing is quantized and geometry round-trips exactly.

The tangent and binormal were identified by rebuilding the tangent frame
from each triangle's bone-local positions and UVs and correlating: the
stored vectors line up with the UV-derived tangent and binormal
respectively (mean dot +0.87 and +0.90, cross terms ~0.00), which also
places them in bone space rather than model space.

That closing colour is zero in all 1305 vanilla files, so it cannot be
read off directly, and it was long treated here as reserved padding.
Three things place it: it is per *vertex* rather than per influence (45%
of vanilla vertices carry two influences, and a flat 16 bytes per vertex
is what parses every file to the byte); it sits immediately after the
tangent frame, exactly where a rigid-model vertex keeps its RGBA colour,
and is exactly its size; and CA's older `testdata` files — the same
container from before the colour channel existed — do not have it, nor do
the rigid objects in their attachment section. So it is the same channel
the rigid model gained going from 14 to 18 floats.

**Attachments.** After the last part comes a second section: the props a
unit hangs off a single bone. Each entry is a name and a bone index in
front of an ordinary `.animatable_rigid_model` object, so the same reader
handles both. It is a lone zero word in all but six vanilla files, which
is why it too read as padding at first — but
`unitmodels/euro_equipment.variant_weighted_mesh` (in both games) is 134
of them and *no* skinned parts at all: every musket, spear and flagpole
in the game. Those import as rigid objects bound to their bone with a
Child Of constraint; on the way out, an object with no vertex groups goes
back into this section rather than becoming a skinned part.

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
are built from `unitmodels/textures/<unit>_diffuse.dds` by convention. A
headerless variant (12 files) drops the magic, version and parameter
blocks and opens straight at the part table; it gets synthetic version 0,
the same trick `anim_format` uses for the headerless `.anim`.

### Rigid-model object versions

The rigid-model container has been around since before Empire, and its
object shrinks as you go back. Everything below `uv2` sits at the same
offset in all of them, so one reader covers the lot:

| Object version | Texture slots | Params | Vertex |
| --- | --- | --- | --- |
| 5, 4 | 4 (first 3 flagged) | yes | 20 floats |
| 3 | 4 (first 3 flagged) | no | 20 floats |
| **2** | **3, all flagged** | no | **18 floats** — no `uv2` |
| **1** | **1, unflagged** | no | **18 floats** — no `uv2` |
| **0 (headerless)** | **1, unflagged** | no | **14 floats** — no `uv2`, no colour |

Each step adds exactly one thing to the one before it.

Version 1 is the Empire/Napoleon object: 23 files across the two games
(12 distinct — Napoleon ships the same set bar the naval cannon), being
the campaign mountains, the flagpole, the boarding plank and that cannon.
Version 0 goes further and drops the per-object magic and version words
entirely, so the file's object count runs straight into the first name;
it is detected by the *absence* of the magic where the first object
should begin. One shipping file uses it
(`enginemodels/cannon_test_model`). Version 2 sits between 1 and 3, and
only CA's `testdata/fence` models use it.

Losing the magic costs the headerless reader its landmark for the
bone-index lookahead, so for those files whether the objects carry one is
settled per *file* rather than per object: both readings are tried and
the one that consumes the file exactly is kept. Vanilla has examples
either way — `cannon_test_model` has bone indices, `testdata`'s `loki`
and `victory_test` (18 and 17 objects) do not.

### `.rigid_model_animation`

Not a new container at all: it is the `.animatable_rigid_model` object
list followed by a whole headerless `.anim`, used for self-contained
animated props — the destruction sequences for cannon carriages, ships
and campaign buildings. All 725 Empire and 784 Napoleon files round-trip
with no new format code. Since the skeleton travels inside the file,
there is nothing to import first and nothing to guess.

## Preservation

Obscure fields you never see — padding bytes, shader parameter blocks,
raw transform matrices, the junk after a fixed string's terminator — are
preserved rather than regenerated. The RMV2 writer also re-parses its own
output before it touches disk, so a file that would not load back is
never written.

The one thing deliberately *not* preserved is
`rmv2_format.EXPORT_SIGNATURE`, which stamps `'R', 'b', 0x00` over three
bytes in every LOD header on export. Those three bytes are struct
alignment rather than a field — CA's own developer gave the original C
struct, and vanilla files back it up: `gen_tree_oak_large_03` repeats the
same stale triple across all three LODs while the quality byte in front
of it varies. RPFM reads all four bytes as one `u32` and casts to `i32`,
so the signature's last byte is 0 to keep the value positive.
