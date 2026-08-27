# Format notes

The binary layouts this add-on reads and writes, described from the
current versions backwards: `.rigid_model_v2` v8 and `.anim` v8 in full,
with a note at each field that an older version does not have. The
pre-Rome 2 formats are different enough to have their own section at the
end.

For what is supported and what is not, see the [README](../README.md).
For measured fidelity, see [CORPUS.md](CORPUS.md).

## Conventions

All data is little-endian. Coordinates are in the game's space —
right-handed, Y-up — and are converted to Blender's on import.

Strings come in three forms:

| Form | Where |
| --- | --- |
| Fixed-width, zero-padded UTF-8 | Names and paths in RMV2 v6 – v8 |
| Fixed-width, zero-padded UTF-16LE at double width | The same fields in RMV2 v5 and in Shogun 2's v1 – v3 |
| Length-prefixed | `.anim`: a u16 byte count then UTF-8 (v5+), or a u16 *character* count then UTF-16LE (v1, v4) |

A fixed-width field keeps whatever bytes were in memory **after** its
terminator, and this add-on preserves them. Zero-padding such a field on
write changes the file: 163 terrain tiles and 33 trees stop re-saving
identically if it is done.

## Coordinates and the pivot

The game is Y-up and effectively left-handed, so the conversion to
Blender's Z-up right-handed space carries exactly one reflection:

```
blender = (-x, -z, y)
```

Triangle winding is reversed to compensate and the UV V axis is flipped.
What you see in Blender matches the in-game orientation rather than a
mirror of it. Export reverses all of it.

RMV2 positions are stored **relative to a pivot** that the game adds back
at render time. The importer puts the pivot at the object's origin; on
export the object's world translation becomes the pivot, and rotation and
scale are baked into the vertices. Shogun 2 has no pivot field — its
meshes live in bone space — so there the translation goes into the
vertices too.

---

# `.rigid_model_v2`

```
RmvFileHeader        140 bytes
RmvLodHeader          28 bytes  x lod_count
per mesh:
    RmvCommonHeader   80 bytes
    material header   variable
    vertex block      vertex_count * stride
    index block       index_count * u16
```

Meshes are stored LOD by LOD, each LOD header pointing at the first mesh
section of its own run.

### The parent matrix index is the collider's bone

The `i32` after the matrix index, which AssetEditor calls
`ParentMatrixIndex`. Reading it as "the bone above the one this mesh
rides" is wrong, and the whole of Warhammer 3 says so.

Sweeping all **23 618** vanilla models (the 111 packs in the game's own
`data/manifest.txt`, extracted with RPFM so the compressed entries are
covered; no parse failures) finds **132 246** meshes carrying the field
and exactly **81** where it is not `-1`. Every one of those 81:

* is a mesh named `collider_*`;
* sits in one of six `*_cloth_cloak_01.rigid_model_v2` files, across the
  `humanoid01`, `humanoid01c` and `humanoid01e` skeletons;
* has its own **matrix index at `-1`** — so this is not a parent *of*
  that bone, it is the mesh's own attachment, kept in the other slot.

58 of the 81 are named after a body part, and all 58 name exactly the
bone their value points at:

| Collider mesh | Value | Bone in the skeleton |
| --- | --- | --- |
| `collider_root` | 1 | `root` |
| `collider_spine_0` | 8 | `spine_0` |
| `collider_upperleg_left` | 9 | `upperleg_left` |
| `collider_lowerleg_right` | 12 | `lowerleg_right` |
| `collider_spine_2` | 18 | `spine_2` |
| `collider_upperarm_right` | 24 | `upperarm_right` |
| `collider_skirt_back_left_1` | 66 | `skirt_back_left_1` |

The remaining 23 are numbered rather than named (`collider_007`,
`collider_soft_020`), so they cannot confirm themselves, but they
resolve to the same sort of bones — arms, spine, legs, skirt.

So a cloth cloak ships as the simulated cloth, the rendered cloak, and a
set of rigid proxy volumes for the sim to bounce the cloth off; each
proxy names the bone it rides here. Both other meshes in those files
leave the field at `-1`.

The other games have not been swept. The tooling is in
[tools/](../tools/): `python tools/sweep_field.py parent_matrix_index
<game> "<install>"`.

## File header — 140 bytes

| Offset | Type | Field |
| --- | --- | --- |
| 0 | `char[4]` | `"RMV2"` |
| 4 | `u32` | Version |
| 8 | `u32` | LOD count |
| 12 | `char[128]` | Skeleton name, empty for unrigged models |

**In older versions:** v5 is 268 bytes, its skeleton name a 256-byte
UTF-16 field. v1 – v3 have only the first 12 bytes here and keep the
skeleton name after the LOD table instead.

## LOD header — 28 bytes, one per LOD

| Offset | Type | Field |
| --- | --- | --- |
| 0 | `u32` | Mesh count |
| 4 | `u32` | Total vertex bytes in this LOD |
| 8 | `u32` | Total index bytes |
| 12 | `u32` | Absolute offset of the first mesh section |
| 16 | `f32` | Camera distance at which this LOD takes over |
| 20 | `u32` | LOD level |
| 24 | `u8` | Quality level |
| 25 | `u8[3]` | Struct alignment padding |

**In older versions:** v5 and v6 stop at 20 bytes — no LOD level, quality
or padding.

Two things about this header do not behave as declared:

- **The byte totals are not always right.** Some materials — bow waves,
  and one of the terrain-tile ids — ship them zeroed even though real
  geometry follows. A file that disagrees with its own geometry keeps the
  declared pair and writes it back unchanged.
- **The three padding bytes are compiler alignment, not a field.**
  `quality_level` is a lone `uint8_t` in CA's own struct, and the bytes
  after it hold stale memory: `gen_tree_oak_large_03` repeats the same
  triple across all three of its LODs while the quality byte in front of
  it varies. This add-on stamps `'R', 'b', 0x00` there
  (`rmv2_format.EXPORT_SIGNATURE`) so an exported file is recognisable.
  The last byte is 0 because at least one tool reads all four bytes as a
  single `u32` and casts it to `i32`; keeping the sign bit clear keeps
  that reading positive.

## Mesh common header — 80 bytes

| Offset | Type | Field |
| --- | --- | --- |
| 0 | `u16` | Material id — see [ModelMaterialEnum](#modelmaterialenum) |
| 2 | `u16` | Render flag |
| 4 | `u32` | Mesh section size, from the start of this header |
| 8 | `u32` | Vertex block offset, relative to this header |
| 12 | `u32` | Vertex count |
| 16 | `u32` | Index block offset, relative to this header |
| 20 | `u32` | Index count |
| 24 | `f32[6]` | Bounding box: min xyz then max xyz |
| 48 | `char[12]` | Shader name, e.g. `default_dry` |
| 60 | `char[10]` | Shader values |
| 70 | `char[10]` | Zero |

**In older versions:** v5 is 112 bytes, its three trailing string fields
at double width. v1 – v3 stop at 48 bytes — the shader fields do not
exist.

The material id chooses the header that follows, and the gap between the
end of this header and the vertex block is how large that material header
must turn out to be. A material that does not consume its declared size
exactly is a parse error.

Three section shapes deviate:

- **A mesh with no vertex block.** Warhammer 3's decals declare a vertex
  count and ship no vertices; the game builds the geometry. The count is
  kept so the file re-saves as it was read.
- **Extra data after the index block.** Attila's ropes follow their
  indices with a list of u16 that is plainly more indices but in no
  layout described here. The section size is authoritative, so whatever
  is past the indices is kept with the mesh.
- **Indices before vertices.** A tree's furthest LOD is a generated
  billboard — a flat card in the 12-byte position-and-uv layout — and it
  lays its section out as material, index block, vertices. Its section
  size stops at the vertex block rather than covering it, so the vertices
  run past the end of their own section; that works because such a mesh
  is always the last one in the file. The size is written back rather
  than recomputed. Detection is deliberately narrow: `index_offset` below
  `vertex_offset` with the indices exactly filling the gap
  (`RmvModel.indices_first`).

## The weighted material header — 860 bytes plus lists

Everything except the two terrain materials and the short headers below
uses this layout: the weighted family, `default_type`, decals, dirtmaps,
cloth, trees, grass and the rest.

| Offset | Type | Field |
| --- | --- | --- |
| 0 | `u16` | Vertex format — see [VertexFormat](#vertexformat) |
| 2 | `char[32]` | Model name |
| 34 | `char[256]` | Texture directory |
| 290 | `char[256]` | Filters |
| 546 | `u8[2]` | Padding |
| 548 | `f32[3]` | Pivot |
| 560 | `f32[12] x 3` | Three 3x4 transform matrices |
| 704 | `i32` | Matrix index — the bone this mesh is welded to, -1 for none. Shown as **Bone Index** in Blender, which is what the other three containers call it |
| 708 | `i32` | Parent matrix index — the bone a **cloth-physics collider** follows. See below. Shown as **Collider Bone** |
| 712 | `u32[6]` | Counts: attachment points, textures, string params, float params, int params, vec4 params |
| 736 | `u8[124]` | Padding |

Then the six lists, in that order:

| List | Entry |
| --- | --- |
| Attachment points | `char[32]` name, `f32[12]` matrix, `i32` bone index — 84 bytes |
| Textures | `i32` type, `char[256]` path — 260 bytes |
| String params | `i32` index, then a u16-length UTF-8 string |
| Float params | `i32` index, `f32` value |
| Int params | `i32` index, `i32` value |
| Vec4 params | `i32` index, `f32[4]` value |

**In older versions:** v5's header is 1404 bytes, with every string field
at double width — a 64-byte model name, 512-byte texture directory and
filters, 116-byte attachment points and 516-byte texture entries. The
field order and count are identical, so the same parser covers both.
v1 – v3 do not have this header at all; see
[Shogun 2's materials](#shogun-2-materials).

### Parameter slots

Parameters are addressed by index rather than by name:

| List | Index | Meaning |
| --- | --- | --- |
| Float | 0, 1 | UV scale X, Y |
| Int | 0 | Alpha mode: 0 opaque, 1 alpha blend/test |
| Int | 1 | Decal |
| Int | 2 | Dirt |
| Vec4 | 0 | Decal texture transform |

Indices outside this list are read and written back unchanged.

### Where a .variant_part_mesh keeps its material

There are no textures in this format and no material id. A material is
three names, and **where** they sit depends on the vertex format, not on
the version:

| | Library (vertex format 2) | Normal (vertex format 0 or 1) |
| --- | --- | --- |
| Per part | name, bone index, **its own three material names** | nothing - parts carry no names |
| Trailer | the skeleton name only, in an 80-byte field | `_TRAILER_SLOTS[version]` names |

`_TRAILER_SLOTS` is `{0: 1, 2: 1, 3: 4}`, so only **version 3** keeps
material names in the trailer at all: four slots, the first of which is
the skeleton. A v0 or v2 non-library file has one slot, the skeleton, and
therefore no material names anywhere.

That split follows what the two shapes *are*. A normal file is one prop
whose parts are its LOD levels, so one set of names covers it. A library
is many unrelated props sharing a file - `equipment/mesh1` holds 52 of
them across 142 parts - so each part needs its own.

The Blender side puts them where the file does: on the mesh for a library
part, on the model for a normal file.

## The short material headers

Several material families have a short fixed header of their own instead
of the weighted layout. Ids are not stable across the series — the
terrain-tile material is 66 or 97 in the Rome 2 era and 101 in Warhammer,
and the decal family runs 67, then 87, then 95 — so dispatch goes by id
**and** by the header size the section implies.

| Family | Ids | Layout |
| --- | --- | --- |
| Terrain tiles | 101, 66, 96, 97 | A 64-byte name and five or six u32 |
| Custom terrain | 49 | A 256-byte texture path |
| Projected decals | 67, 87, 95, 100 | A texture path and one, nine or ten floats |
| Interface banners | 29, 30 | A 256-byte model name and eight u32, zero in every observed file |
| No header at all | bow waves, one terrain-tile id | Straight from the common header to the vertices, so the vertex layout comes from the stride |

**Cloth, weighted cloth, collision shapes and rope** (ids 58, 60, 62, 93)
are an ordinary weighted material followed by a block of their own: for
cloth and rope a simulation graph — a count, then triples of two vertex
indices and a rest length — and for a collision shape twenty bytes.
Nothing in Blender could rebuild either, so the block is kept exactly as
read and written back with its material. The allowance is limited to
those four ids; anywhere else, a material that does not consume its
declared size stays a parse error.

Five families are **not decoded**: ids 26, 40, 45, 54, 57 and 84 —
`non_renderable`, campaign settlement pieces, point lights, statues and
Attila's ship night lights. Each is plainly a name or path followed by a
run of words, but every observed example has those words at zero, so
there is nothing to check a layout against.

## Vertex layouts

Positions are half4 with the W component as a scale factor, except where
noted. Normals, tangents and binormals are u8x4 in the packed layouts and
half4 in the wide ones.

| Format | Stride | Layout |
| --- | --- | --- |
| Static | 32 | `pos` half4, `uv` half2, `uv2` half2, `normal` `tangent` `binormal` `col` u8x4 |
| Weighted | 28 (32 in v8) | `pos` half4, 2 bone indices, 2 weights, `normal` u8x4, `uv` half2, `binormal` `tangent` u8x4, v8: `col` u8x4 |
| Cinematic | 32 (36 in v8) | The same with 4 bone indices and 4 weights |
| Collision | 24 | `pos` float3, `normal` float3 |
| Position16 | 16 | `pos` float4 |
| CustomTerrain | 36 | `pos` float4, `normal` float4, `uv` half2 |
| CustomTerrain2 | 48 | The same plus three u8x4 colours |
| Vegetation | 60 | `pivot` half4, `pos` half4, `normal` `tangent` `binormal` half4, `uv` half2, `wind` half8 |
| TreeBillboard | 28 | `pos` half4, `normal` half4, `uv` half2, four unused halves |
| Grass | 28 | `pos` half4, `uv` **float2**, `normal` `tangent` `binormal` u8x4 |
| PositionHalf | 8 | `pos` half4 and nothing else |
| PositionUV | 12 | `pos` half4, `uv` half2 |
| Sway | 20 | See below |

**The colour channel is v8's addition.** A weighted or cinematic vertex
gains four bytes of vertex colour in v8 and has none before it, which is
why the same format id has two strides.

**The sway vertex** (format 12, stride 20) is two half4s whose W
components carry the UV between them — position + U, then normal + V —
followed by a u8x4 colour whose **alpha is the wind-sway weight**. Unlike
every other half position in the format the XYZ is *not* scaled by W,
because W is the U coordinate; adding the material's pivot to the raw
half3 reproduces the header's bounding box exactly.

**Channels with no Blender field** ride through as point attributes,
visible in the spreadsheet and read back on export: a vegetation vertex's
rest position (`rmv2_pivot`) and eight wind weights (`rmv2_wind_0`…), a
bow wave's second position (`rmv2_pos2`), custom terrain's two spare
colours. The eight wind weights are halves of unknown meaning; five of
the eight are constant per mesh in every observed file.

## Enums

### VertexFormat

The `u16` at the head of the material. The values that occur in files:

| Value | Name | Notes |
| --- | --- | --- |
| 0 | Static | |
| 1 | Collision | |
| 3 | Weighted | 2 influences |
| 4 | Cinematic | 4 influences |
| 5 | Position16 | Also declared by 8-byte and 28-byte layouts |
| 6 | CustomTerrain | Also declared by the 60-byte vegetation vertex |
| 7 | — | Rome 2's trees: 12-byte and 28-byte layouts |
| 8 | — | Rome 2's water planes, 12 bytes |
| 12 | — | Warhammer's sway vertex, 20 bytes |
| 13 | CustomTerrain2 | |

Values 2 and 9 – 11 have no known meaning and are not written by any
observed file.

**The id alone does not identify the layout.** Several ids cover more
than one, and the game tells them apart by material and stride. The
reader resolves the pair (declared id, actual stride); a material with no
vertex-format field at all is resolved by stride alone. Layouts that the
file's own field does not name are given ids of 100 and up internally,
and `declared_format_id()` maps them back to what CA writes so a mesh
built in Blender declares a number the game has seen.

### ModelMaterialEnum

The `u16` at the head of the common header. Named values:

| | | | |
| --- | --- | --- | --- |
| 22 `bow_wave` | 26 `non_renderable` | 29 `texture_combo_vertex_wind` | 30 `texture_combo` |
| 31 `decal_waterfall` | 32 `standard_simple` | 34 `campaign_trees` | 38 `point_light` |
| 45 `static_point_light` | 46 `debug_geometry` | 49 `custom_terrain` | 58 `weighted_cloth` |
| 60 `cloth` | 61 `collision` | 62 `collision_shape` | 63 `tiled_dirtmap` |
| 64 `ship_ambientmap` | 65 `weighted` | 67 `projected_decal` | 68 `default_type` |
| 69 `grass` | 70 `weighted_skin` | 71 `decal` | 72 `decal_dirtmap` |
| 73 `dirtmap` | 74 `tree` | 75 `tree_leaf` | 77 `weighted_decal` |
| 78 `weighted_decal_dirtmap` | 79 `weighted_dirtmap` | 80 `weighted_skin_decal` | 81 `weighted_skin_decal_dirtmap` |
| 82 `weighted_skin_dirtmap` | 83 `water` | 84 `unlit` | 85 `weighted_unlit` |
| 86 `terrain_blend` | 87 `projected_decal_v2` | 88 `ignore` | 89 `tree_billboard_material` |
| 91 `water_displace_volume` | 93 `rope` | 94 `campaign_vegetation` | 95 `projected_decal_v3` |
| 96 `weighted_texture_blend` | 97 `projected_decal_v4` | 98 `global_terrain` | 99 `decal_overlay` |
| 100 `alpha_blend` | 101 `TerrainTiles` | | |

The unlisted values have no name here. An unknown id is read as a
weighted material if its header size fits one, and written back with the
id it had.

Note that a name is not a promise about the era: ids are reused. 66, 96
and 97 carry terrain tiles in the Rome 2 era whatever their names say,
which is why the dispatch weighs header size alongside id.

### TextureType

The `i32` in front of each texture path:

| | | |
| --- | --- | --- |
| 0 Diffuse | 1 Normal | 3 Mask |
| 5 Ambient_occlusion | 7 Tiling_dirt_uv2 | 10 Skin_mask |
| 11 Specular | 12 Gloss | 13 Decal_dirtmap |
| 14 Decal_dirtmask | 15 Decal_mask | 17 Diffuse_damage |
| 27 BaseColour | 29 MaterialMap | |

Unlisted values are kept as raw numbers and written back unchanged; the
Object panel exposes them as *Other (raw id)*.

## Materials in Blender

Textures are resolved against the texture root and wired into a
Principled BSDF: BaseColour/Diffuse, Normal, Gloss, Specular. Three
CA-specific packings get node graphs rather than being fed to Blender
raw:

- **Normal maps** are packed R = 1, G = Y, B = 0, A = X, with Z
  reconstructed. A node group decodes them.
- **MaterialMap** — R = metallic, G = roughness. Takes priority over
  Gloss when both are present.
- **Mask** — the RGB channels blend three adjustable player-colour
  swatches over the base colour (faction tinting); alpha drives emission
  strength.

---

# `.anim`

One format covers both skeletons and animations. A skeleton file carries
a few identical copies of the bind pose as ordinary frames, so it is
structurally indistinguishable from a short animation; the importer
decides by looking at the file and the scene.

```
header
bone table
per part:
    per-bone rates and ranges
    static frame
    dynamic frames
```

## Header

| Type | Field |
| --- | --- |
| `u32` | Version |
| `u32` | Header type — see below |
| `f32` | Frame rate |
| string | Skeleton name |
| `u32` | Flag count, then that many strings |
| `f32` | Total playtime, seconds |

**In older versions:** the flag strings are v7+. v4 writes its strings as
UTF-16 with a character count.

**The header type word is not a constant.** It is usually 1, but of
Warhammer 3's 34,984 animations 51 carry 2 and 41 carry 0, and Three
Kingdoms uses all three values freely. In Warhammer, 291 of 6975 files
carry 0, and nearly all of those are animations that ride on a rigid
model — buildings, chariots, war machines — rather than on a character
rig. That is the closest this field comes to a known meaning.

## Bone table

| Type | Field |
| --- | --- |
| `u32` | Bone count |
| per bone: string | Name |
| per bone: `i32` | Parent index, -1 for a root |

v8 follows the table with one further `u32`.

## Parts

v8 splits its data into parts, which play **back to back**: part 2 starts
where part 1 ends. v5, v6 and v7 have exactly one part.

Each part holds a translation channel and a rotation channel per bone. In
v5 – v7 those are mapping tables of `i32`, one per bone:

| Value | Meaning |
| --- | --- |
| -1 | Not animated — use the skeleton's bind pose |
| 0 – 9999 | Index into each dynamic frame |
| 10000+ | Index into the static frame, minus 10000 |

**A v8 part has no mapping tables.** It carries a per-bone `int8` *rate*
instead, whose sign does the mapping table's job — negative static,
positive dynamic, zero not animated — and whose magnitude says how the
channel is packed:

| Channel | Rate | Packing |
| --- | --- | --- |
| Translation | 12 | Three `f32` |
| Translation | 3 | Three `int8`, decoded through a per-bone range |
| Rotation | 8 | Four `int16`, value / 32767 |
| Rotation | 4 | Four `int8`, decoded through a per-bone range |

The ranges follow the rates as `(n, 2, 3)` and `(n, 2, 4)` float arrays —
a base and a scale per bone. Their length does not follow from the rates:
CA writes a count of its own choosing, longer than the last ranged bone
in about a third of parts, so ranges are kept exactly as read.

A part built from scratch gets the uncompressed rates, 12 and 8, which
need no ranges and lose nothing. That makes an exported v8 animation two
to three times the size of CA's, and identical in content.

### Frames

| Type | Field |
| --- | --- |
| `u32` `u32` | Static frame: position count, rotation count, then the frame |
| `i32` `i32` `i32` | Dynamic position count, rotation count, frame count |
| | Then that many frames |

A frame is `pos_count` `f32[3]` translations followed by `rot_count` xyzw
quaternions. **In older versions:** the static frame is v7+; v5 and v6
have none.

A part whose pose is held entirely in the static frame still writes a
frame-count word, and that word is `round(duration * frame_rate) + 1` —
the length the animation would have had — not the 3 it usually reads as.

### Precision

**v8 frames decode at float64, and must.** A byte-packed channel decodes
as `base + (byte / 127) x scale`, and Warhammer 3 has bones whose base
dwarfs their scale: 0.7071 against 4.2e-06 is a byte step of 3.3e-08
where float32 resolves 6e-08. In float32 adjacent bytes collapse onto one
value and about 1.6% of files cannot be written back as they were read.
Every other quantization here — int16 / 32767 — fits float32 exactly.

Quaternions are clamped **after** being scaled to int16, not before:
clipping to ±1.0 first turns a stored -32768 into -32767.

## Version 4

Rome 2's original, dropped during that game's own run. The header above
with UTF-16 strings, and in place of the mapping tables:

| Type | Field |
| --- | --- |
| `u32` | Translation bone count, then that many bits packed into u32 words, low bit first |
| `u32` | The same again for rotation |
| `u32` | Frame count |
| | `frame_count * bone_count * 28` bytes: an `f32[3]` translation and an xyzw `f32` quaternion for every bone, in bone order |

So v4 stores every bone in every frame and quantizes nothing — the
compression v5 introduces is the whole point of the version bump.

The two bitfields are not mappings; a v4 file stores every bone whatever
they say. In 24 of Rome 2's 31 files the set bits are exactly the bones
that move, and in the other 7 a few bones move with their bit clear, so
they read as the channels the animator meant to drive rather than as
anything a decoder can act on.

**Version 6 occurs in no observed file** of any game swept. It is read
and written from the versions either side of it and has never met real
data.

## The event block

Shogun 2's `.anim` files carry an event block — animation markers such as
`FIRE_TIME` and `FIRE_POSITION` — and so do 59 Rome 2 v5 cutscene
animations, where it is always empty. Events ride on the armature and are
written back.

---

# The pre-Rome 2 formats

Shogun 2, Empire and Napoleon share a container family of their own.
None of it is described by TheAssetEditor.

## `.rigid_model_v2` v1 – v3

Versions 1 and 2 are Shogun 2's; version 3 is the same layout carried
into Rome 2, which uses it for the 3D user-interface models it inherited
and nothing else. What differs from the modern file:

- The **file header is 12 bytes** — magic, version, LOD count. The
  skeleton name is a 512-byte UTF-16 field *after* the LOD table,
  preceded by a `u32` that is zero in every observed file.
- The **common header is 48 bytes** — the modern one without its three
  shader fields.
- **No per-vertex skinning.** A mesh is welded to exactly one bone, the
  material's bone index, and stored in that bone's space. Import binds
  each mesh to its bone with a Child Of constraint; export writes the
  vertices back in bone space.

### Shogun 2 materials

A material here is a run of fixed-width fields whose composition depends
on the material id, rather than the tagged list of counts the modern
format uses. **It carries no vertex-format field**, so the layout is
identified by stride:

| Stride | Layout |
| --- | --- |
| 12 | Position and one UV, no tangent frame |
| 24 | Bow wave: two half4 positions, a UV and a float |
| 28 | Static with one UV channel |
| 32 | The modern static vertex |
| 44 | Static with float32 positions and UVs |
| 60 | Rome 2's v3 shrubs and hedges |

Because the file has no field for it, the layout a mesh was imported with
is stamped on the object and written back: its material expects that
exact stride.

## `.anim` v1 and v0

| Type | Field |
| --- | --- |
| `u32` | Version, always 1 |
| `f32` | Frame rate |
| `f32` | Total playtime, seconds |
| `u32` | Bone count, then per bone a UTF-16 name with a character count and an `i32` parent |
| `u32` | Frame count, then `frame_count * bone_count * 28` bytes: an `f32[3]` translation and an xyzw `f32` quaternion per bone |
| `u32` | Event count, then per event a `u32` string count and that many UTF-16 strings |

Every bone is animated in every frame, so there are no mapping tables, no
static frame and no parts.

A minority of files — campaign pieces and a few reference skeletons —
have **no version field at all**: they begin at the frame rate and carry
ten floats per bone per frame instead of seven. The extra three are 0.001
in every observed file and their purpose is unknown. They are given the
synthetic version 0, which is never written into a file; the loader
recognises them by checking whether the first four bytes make a plausible
frame rate when no known version number is found.

Shogun 2 has **no separate skeleton files**. Every `.anim` carries the
full bone table, and models name the `.anim` they belong to rather than
the other way round.

## `.animatable_rigid_model` and `.rigid_model`

A flat list of objects with no LOD tree of its own — the LOD ladder is
separate `..._lod1` / `..._lod2` files.

`.animatable_rigid_model` gives each object a bone index, which is the
whole of what "animatable" adds: jointed props whose parts ride bones,
such as siege engines and ballistae. `.rigid_model` is the same container
without it, closing instead with a bounding box, and holds static
geometry. Objects are matched to their form by looking ahead in the file
rather than by trusting its name.

Which is why the two menu rows mean different things in each direction.
Exporting, they are a real choice, and it belongs in the menu rather than
in which extension the user happened to type. Importing, they are a
browser filter over one operator: the file decides, so either row opens
either form.

The object shrinks as the versions go back. Everything below `uv2` sits
at the same offset in all of them, so one reader covers the lot:

| Object version | Texture slots | Params | Vertex |
| --- | --- | --- | --- |
| 5, 4 | 4 (first 3 flagged) | yes | 20 floats |
| 3 | 4 (first 3 flagged) | no | 20 floats |
| 2 | 3, all flagged | no | 18 floats — no `uv2` |
| 1 | 1, unflagged | no | 18 floats — no `uv2` |
| 0 (headerless) | 1, unflagged | no | 14 floats — no `uv2`, no colour |

Version 1 is the Empire/Napoleon object: 23 files across the two games,
12 of them distinct — the campaign mountains, the flagpole, the boarding
plank and a naval cannon. Version 2 sits between 1 and 3 and only CA's
`testdata/fence` models use it. Version 0 drops the per-object magic and
version words entirely, so the object count runs straight into the first
name; it is detected by the *absence* of the magic where the first object
should begin. One shipping file uses it
(`enginemodels/cannon_test_model`).

### The named parameter block

From object version 4 each object carries its own block of named shader
values: a count, then a u16-length UTF-16LE name and one `f32` per entry,
then the same again for four-component entries. `light_scale`,
`offsetu0`, `bumpfactor`, `specpower`, `glossfactor`, `specfactor`.

The four-component list is colours. Across the sample corpus it holds
exactly three names — `colourmapfactor`, `rimcolor` and `specfactor` —
and every component of every one of them falls in 0..1, so Blender draws
them as RGBA swatches. The property is soft-ranged rather than clamped,
because "no vanilla file leaves 0..1" is a fact about the files read so
far, not a rule the format states.

It is **per object**, not per file, and vanilla proves the difference:
Shogun 2's `naval_cannon_12lb_lod4` writes 13 float parameters and 3
vec4s on three of its four objects, and an empty block on the fourth. The
Blender side therefore keeps it on the mesh object, and an object that
had none is written back with none — see
[capabilities.py](../io_scene_rmv2/capabilities.py).

`.variant_part_mesh` and `.variant_weighted_mesh` carry the same kind of
block, but once for the whole file, ahead of the parts — so theirs is
kept on the root collection instead. The sets differ per model rather
than being a fixed default: Empire's `euro_line_infantry_lod4` has 13
float parameters, `euro_equipment` 11, and Napoleon's
`battleoutfit_lod1` a different 9 with no `offsetu0`/`offsetv0` at all.
Every `.variant_part_mesh` read so far carries an empty block.

`.rigid_model_v2` has parameter lists too, but addresses them by index
rather than by name (see [Parameter slots](#parameter-slots)), so they
are a different thing and stay in the preserved `extra_json`.

Losing the magic costs the headerless reader its landmark for the
bone-index lookahead, so for those files whether the objects carry one is
settled per *file*: both readings are tried and the one that consumes the
file exactly is kept. There are examples either way —
`cannon_test_model` has bone indices, `testdata`'s `loki` and
`victory_test` (18 and 17 objects) do not.

## `.variant_part_mesh`

The format Shogun 2's units are built from: one file per helmet, torso,
mask or saddle, assembled at runtime by the variant mesh definitions.

In Blender it behaves like any skinned mesh — vertex groups plus an
armature modifier — because that is what it is: up to **two weighted bone
influences per vertex**, effectively CA's later Weighted2. Bone indices
address the reference skeleton directly, with no intermediate palette.

The storage has no counterpart elsewhere. Each vertex holds its position
**once per influence, each in that influence's own bone space**, with one
byte giving the first influence's weight. Single-influence vertices leave
the second copy zeroed, which is how the two cases are told apart. **No
model-space position exists anywhere in the file**, which is why the
armature is required on import and export alike.

The fallback bind pose, used when no armature is available, works because
every two-influence vertex is one sample of the rigid transform between
two bone spaces: fit those, span the resulting graph, and the pose falls
out of the file alone.

| Vertex format | Stride | Contents |
| --- | --- | --- |
| 0 Rigid | 64 | float32 positions, no skinning |
| 1 Skinned | 48 (40 in v0) | Two influences; v0 has no tangent frame on the second |
| 2 Rigid, named | 64 | As 0, each part prefixed with its own names |

The rigid layout covers equipment, crests and blank parts and round-trips
bit-exactly, since nothing is quantized. It also carries a second UV set
(ambient occlusion), imported as `UVMap_1` when non-zero. Which layout
gets written comes from each mesh's own Vertex Format setting, stamped at
import.

A rigid part is not skinned, but it is not in model space either: it
rides one bone whole, named by the signed index in its own header (-1 =
none), and its vertices are stored **in that bone's space** — the same
arrangement as an `.animatable_rigid_model` object. `cine_farmerhat`'s
352 vertices sit in a 0.45 m box around the origin, not at head height.
So a rigid part imports with a Child Of constraint on that bone and no
vertex groups at all, and the constraint is what export reads back.

The mounts are ordinary skeleton bone indices, not a separate palette:
on `man_shogun` 1 and 3 are `Weapon1`/`Weapon3`, 14 is `Spine2` (every
`_bp` backpack) and 20 is `Sashimono` (the banner poles). The three
`Weapon` bones are parentless and sit at the world origin in the
reference pose — they are placed by whatever animation is playing — so a
prop on one of them belongs at the origin until an animation moves it.

Because the parts are the ladder, Blender's Generate LODs (Decimate)
drives this format exactly as it drives the `.rigid_model_v2` LOD table,
off the same Auto-LOD Override rows. Library files are the exception:
their parts are named in the file, so a generated level would write one
prop's stored name twice, and the export refuses instead.

**Library files.** A few `variantmodels/equipment/` files pack dozens of
unrelated props into one container — `mesh1.variant_part_mesh` holds 52
props across 142 parts, each with its own name, LOD ladder and attachment
slot. These round-trip in full: one object per part, named
`<prop>_lod<N>`, with every prop's name, slot, material names and
ordering written back.

LOD numbers in those names are whatever the artist used — ladders usually
start at `lod1`, but `rigid_equip_Yumi` starts at `lod2` and two parts in
`mesh1` have no suffix at all — so each prop's levels are numbered from
its own finest rather than from a file-wide floor.

The one thing that does not survive is geometry that never rendered. 19
of 315 named parts carry vertices no triangle references (151 in total);
each sits at exactly the position of a vertex the mesh does use and
differs only in normal, tangent or UV — split vertices left at a seam. 12
parts also carry zero-area triangles referencing them (46, across
`mesh2`'s Boshin rifles). Blender stores neither, so both are dropped and
the importer says so. Nothing that was ever drawn is lost.

## `.variant_weighted_mesh`

What Empire's and Napoleon's units are built from: one file per unit per
LOD, holding every body part its variants can draw (`<unit>_head01`,
`<unit>_body02`, `<unit>_legs01` …). Empire ships 1269, Napoleon 286, and
Shogun 2 exactly one.

The skinning is `.variant_part_mesh`'s idea generalised:

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

So again **no model-space position exists anywhere in the file** and the
armature is required. The influence count varies per vertex, which makes
the stride variable; that, rather than the field layout, is the format's
one real difficulty. Everything is plain float32, so nothing is quantized
and geometry round-trips exactly.

The tangent and binormal are identified by rebuilding the tangent frame
from each triangle's bone-local positions and UVs: the stored vectors
line up with the UV-derived tangent and binormal respectively (mean dot
+0.87 and +0.90, cross terms ~0.00), which also places them in bone space
rather than model space.

The closing colour is zero in all 1305 observed files. Three things place
it: it is per *vertex* rather than per influence (45% of vertices carry
two influences, and a flat 16 bytes per vertex is what parses every file
to the byte); it sits immediately after the tangent frame, exactly where
a rigid-model vertex keeps its RGBA colour, and is exactly its size; and
CA's older `testdata` files, the same container from before the colour
channel existed, do not have it, nor do the rigid objects in their
attachment section. It is the same channel the rigid model gained going
from 14 to 18 floats.

**Variant slots.** One file holds every alternative a unit's variants can
draw, and nothing in the container says which of them are answers to the
same question: the part table is flat and a part carries no slot field.
The name is the whole signal, and it is the same signal CA's own variant
tables use, since the file gives them nothing else to address a part by.
So the importer groups parts by name up to a trailing number —
`<unit>_head01` … `<unit>_head04` are one slot, `Telescope` is a slot of
one — and leaves the lowest-numbered member of each visible, closing the
viewport eye on the rest. `euro_line_infantry_lod4` opens as 8 visible
parts out of 16, which is one soldier. Nothing is deleted and export
writes them all.

**Attachments.** After the last part comes a second section: the props a
unit hangs off a single bone. Each entry is a name and a bone index in
front of an ordinary `.animatable_rigid_model` object, so the same reader
handles both. It is a lone zero word in all but six files —
`unitmodels/euro_equipment.variant_weighted_mesh`, in both games, is 134
of them and no skinned parts at all: every musket, spear and flagpole in
the game. Those import as rigid objects bound to their bone with a Child
Of constraint; on the way out, an object with no vertex groups goes back
into this section rather than becoming a skinned part.

**Older layouts.** CA's `testdata` folders keep three earlier generations
of the container, each dropping one thing from the shipping layout:

| part table | vertex head | colour block | files |
| --- | --- | --- | --- |
| nameless | uv | no | 2 |
| nameless | uv + tangent frame | no | 20 |
| named | uv + tangent frame | no | 4 |
| named | uv + tangent frame | yes | everything that shipped |

The nameless table is a three-word header — part count, total vertices,
total indices — with no names anywhere, and those files skin more heavily
than anything that shipped, up to 11 influences on a vertex where
shipping files stop at 8. Which layout a file uses is decided by trying
each and keeping the one that consumes the file exactly.

There are no texture slots: the part name doubles as the texture-set
name, and the variant system picks the images at runtime, so materials
are built from `unitmodels/textures/<unit>_diffuse.dds` by convention. A
headerless variant (12 files) drops the magic, version and parameter
blocks and opens straight at the part table; it gets synthetic version 0.

## `.rigid_model_animation`

Not a new container: the `.animatable_rigid_model` object list followed
by a whole headerless `.anim`, used for self-contained animated props —
the destruction sequences for cannon carriages, ships and campaign
buildings. Since the skeleton travels inside the file, there is nothing
to import first and nothing to guess.

---

# Using the format layer on its own

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
`weld_loops` (the shared export path).

## Preservation

Padding bytes, shader parameter blocks, raw transform matrices, the junk
after a fixed string's terminator, simulation blocks and unknown
parameter indices are all preserved rather than regenerated. The RMV2
writer re-parses its own output before it touches disk, so a file that
would not load back is never written.

The [named parameter blocks](#the-named-parameter-block) are preserved
*and* editable: they come in as a list in the panel the container puts
them in and go back out as whatever is in that list, empty included.
Everything else in this section is kept verbatim and has no UI, because
it is not meant to be edited.

The RMV2-era layouts follow the C# reference in
[TheAssetEditor](https://github.com/donkeyProgramming/TheAssetEditor)
(`Shared/GameFiles/RigidModel`, `Shared/GameFiles/Animation`), which is
also where the enum names above come from.
