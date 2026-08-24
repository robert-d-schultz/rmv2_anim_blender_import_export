# Total War model & animation formats for Blender

A Blender add-on for Creative Assembly's model and animation files.
Imports and exports meshes, skeletons, skinning, LODs, materials and
animations for every Total War from **Empire (2009)** to **Pharaoh
Dynasties (2023)**.

Every file version it reads, it also writes, and re-saving an unmodified
file reproduces it byte for byte — see [corpus results](docs/CORPUS.md).

|  |  |
| --- | --- |
| **Blender** | 4.2+ as an extension, 3.6+ as a legacy add-on (developed against 5.2) |
| **Download** | [Latest release](https://github.com/robert-d-schultz/rmv2_anim_blender_import_export/releases/latest) |
| **Game files** | Unpack `.pack` archives with [RPFM](https://github.com/Frodo45127/rpfm) |
| **Docs** | [User guide](docs/USER_GUIDE.md) · [Format notes](docs/FORMATS.md) · [Corpus results](docs/CORPUS.md) |

## Install

Download the zip from the
[latest release](https://github.com/robert-d-schultz/rmv2_anim_blender_import_export/releases/latest).

- **Blender 4.2+** — *Edit → Preferences → Get Extensions → Install from
  Disk*, point it at the zip.
- **Blender 3.6–4.1** — *Edit → Preferences → Add-ons → Install*, point
  it at the zip, then tick "Total War RigidModel".

Then set **Texture Root Directory** in the add-on preferences to a folder
of extracted game textures, so imported materials arrive with images.

To build the zip from source:

```
blender --command extension build --source-dir io_scene_rmv2
```

Start with the [user guide](docs/USER_GUIDE.md) — importing a model and
its skeleton is a two-step job, and for the older games the order
matters.

## Supported files

| File | What it holds | Games | Versions |
| --- | --- | --- | --- |
| `.rigid_model_v2` | Meshes, LODs, materials | Rome 2 → Pharaoh | 5, 6, 7, 8 |
| `.rigid_model_v2` | Meshes, LODs, materials | Shogun 2, and Rome 2's UI models | 1, 2, 3 |
| `.anim` | Skeletons and animations | Rome 2 → Pharaoh | 4, 5, 6 †, 7, 8 |
| `.anim` | Skeletons and animations | Shogun 2, Empire, Napoleon | 1, and a headerless variant (0) |
| `.animatable_rigid_model` | Bone-welded props — siege engines, ballistae | Shogun 2, Empire, Napoleon | object 0 – 5 |
| `.rigid_model` | The same, without the bone index | Shogun 2, Empire, Napoleon | object 0 – 5 |
| `.variant_part_mesh` | Skinned unit parts — helmets, torsos, saddles | Shogun 2 | 0, 2, 3 |
| `.variant_weighted_mesh` | Skinned units, one file per LOD | Empire, Napoleon | 1, and a headerless variant (0) |
| `.rigid_model_animation` | An object list plus its own animation | Empire, Napoleon | 3, 4, 5 |

**Everything in that table reads *and* writes** — including the versions
AssetEditor will not write (`.anim` v8, RMV2 v5) and the two Rome 2
shipped with and dropped mid-life, which appear in no reference this
project knows of (RMV2 v3, `.anim` v4). The exporter offers the whole
list, so a model imported from one game can be written out for another.

† `.anim` v6 is implemented from the versions either side of it and has
never been seen in a vanilla file — see [Not supported](#not-supported).

### Which game uses what

| Game | `.rigid_model_v2` | `.anim` | Also |
| --- | --- | --- | --- |
| Empire | — | headerless | `.rigid_model`, `.variant_weighted_mesh`, `.rigid_model_animation` |
| Napoleon | — | headerless | as Empire |
| Shogun 2 | 1, 2 | 1, headerless | `.rigid_model`, `.variant_part_mesh` |
| Rome 2 | 5, 6, and 3 for UI models | 4, 5 | |
| Attila | 6, some 5, four 3 | 5, some 4 | |
| Thrones of Britannia | not surveyed | not surveyed | |
| Warhammer | 7, some 6 | 5 | |
| Warhammer 2 | 7, some 6 | 7, some 5 | |
| Three Kingdoms | 7, 8 | 7, 8 | |
| Troy | not surveyed | not surveyed | |
| Warhammer 3 | 7, 8 | 5, 7, 8 | |
| Pharaoh Dynasties | 7, four 6 | 7, some 5, one 4 | |

Thrones of Britannia and Troy are the two the project has never had on
disk. Neither is expected to bring a new version: Pharaoh carries several
thousand Troy-era meshes and they are all v7.

## Supported vertex layouts

Per mesh you get positions, custom split normals, the full tangent basis,
both UV channels, vertex colours and bone weights. **Every layout the
add-on reads, it also writes.**

| Layout | Bytes | Used by |
| --- | --- | --- |
| Static | 32 | Buildings and props, two UV channels |
| Weighted / Cinematic | 32 / 44 | Skinned meshes, 2 or 4 influences per vertex |
| Collision | 24 | Collision hulls, no UVs |
| Custom terrain ×2 | 36 | Terrain patches, with two spare colour channels |
| Vegetation | 60 | Trees and shrubs: a rest position and eight wind weights |
| Tree billboard | 28 | The flat card a tree's furthest LOD collapses to |
| Grass | 28 | Grass, with float32 UVs |
| Sway | 20 | Warhammer's wind-swayed cloth and leaf cards — the sway weight is the vertex colour's alpha |
| Position (float / half / +UV) | 16 / 8 / 12 | Decals, terrain tiles, water planes |
| Shogun 2 static ×2 | 28 / 44 | Shogun 2 meshes, one UV channel |
| Shogun 2 bow wave | 24 | Ship bow waves, with a second position channel |
| `.variant_part_mesh` skinned / rigid | 48 (40 in v0) / 64 | Shogun 2 unit parts, and their rigid equipment |
| `.variant_weighted_mesh` | variable | Empire and Napoleon units, 1 – 8 influences per vertex |

Channels no ordinary Blender mesh has a slot for — a vegetation vertex's
rest position and wind weights, a bow wave's second position, custom
terrain's spare colours — ride through Blender as **point attributes**
(`rmv2_pivot`, `rmv2_wind_0`, `rmv2_pos2`): visible in the spreadsheet,
editable, and read back on export.

Material types survive as what they are rather than being flattened into
one: weighted, custom terrain, terrain tiles, the projected-decal family,
cloth, rope, collision shapes, bow waves and interface banners each
export as the material they were imported as. Cloth and rope simulation
blocks, which nothing in Blender could rebuild, are preserved verbatim.

## Not supported

| What | Why |
| --- | --- |
| `.rigid_model_v2` v0 | 4 vanilla files, all `castle_01_gate_piece09*`. No skeleton-name field, and meshes beyond what its LOD table declares. Refused with a clear error rather than misparsed |
| `.anim` v6 | **Never observed.** It occurs in no vanilla file of Empire, Napoleon, Shogun 2, Rome 2, Attila, either Warhammer, Warhammer 3 or Pharaoh. Read and write support exists on version numbering alone and has never met real data |
| Encrypted DLC | Empire's 224 Elite Units `.variant_weighted_mesh` files and Pharaoh's 59 `data_special.pack` meshes ship under a cipher, and are deliberately left alone. Napoleon's equivalents are in the clear and work normally |
| A tail of material headers | Ids 26, 40, 45, 54/57 and 84 — point lights, Attila's night lights, statues, settlement pieces. Each is plainly "a name, then a run of words", but every vanilla example has those words at zero, so a layout guess has nothing to be wrong against |
| `.variant_part_mesh` v0 and v2 on export | They read fine but re-export as v3, and the material parameter block, the `crests` attachment and non-default material names are not carried through. Geometry, names, slots and skinning are |
| Medieval 2 and earlier | Different formats entirely, and absent from the C# reference the rest of this is built against |

## Round-trip fidelity

Re-saving an unmodified file byte-for-byte is a tested invariant, checked
against every file the games ship — around 190,000 of them across nine
games. Warhammer, Warhammer 3 and Pharaoh Dynasties read **completely**.
The per-game table, and what each game turned up, is in
[docs/CORPUS.md](docs/CORPUS.md).

## Development

```
python tests/test_format.py                     # format layer, no Blender
blender --background --factory-startup --python tests/test_blender_roundtrip.py
```

The format layer (`rmv2_format.py`, `anim_format.py`, `arm_format.py`,
`vmpf_format.py`, `vwm_format.py`) is bpy-free and usable as a standalone
library — see [docs/FORMATS.md](docs/FORMATS.md) for the layouts and a
usage example.

Binary layout for the RMV2-era formats follows the C# reference in
[TheAssetEditor](https://github.com/donkeyProgramming/TheAssetEditor)
(`Shared/GameFiles/RigidModel`, `Shared/GameFiles/Animation`)
byte-for-byte. The pre-Rome 2 formats are in neither that nor RPFM, and
were reverse-engineered from the games' own packs.
