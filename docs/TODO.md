# Where this stands, and what's left

Written 2026-08-24, after version 1.18.0 (Warhammer, Warhammer 3 and
Pharaoh Dynasties all read completely, every format version writes, and
every vertex layout the add-on reads it can also write). Roughly in
priority order within each group.

## Needs a human

### 1. Manual pass in Blender - done

Driven by hand at last, and it found what headless round-trips cannot:
byte-identity checks bytes, not whether a panel makes sense or whether
an importer left a usable scene behind.

What came out of it, all fixed:

- The five version pickers and the "Other Formats" box are **one
  dropdown** on the model collection, grouped by format, and no export
  dialog has a version field at all - a model is one file and a file has
  one version.
- The panels now show **only what the model's own container can hold**
  (`capabilities.py`), which was the predicted outcome. A
  `.variant_part_mesh` is not offered an alpha mode, and its Vertex
  Format list is its own three rather than all eighteen.
- **Named shader parameters and material names were being dropped** on
  import and overwritten with defaults on export - invisible to every
  sweep, because a sweep never goes through Blender.
- A `.variant_weighted_mesh` of nothing but props **crashed** on import,
  and a merged ladder silently exported one file of four.
- A library `.variant_part_mesh` opened as 52 props stacked on the
  origin; it now opens as one.
- Both ARM formats were being told they rode a skeleton the file never
  names - "mountainb.anim" on a Napoleon mountain with no bones.

Still not done, and still wanting a human: the vegetation point
attributes (`rmv2_pivot`, `rmv2_wind_0`, `rmv2_wind_1`). Whether eight
raw numbers in the spreadsheet are any use to a modder, or want naming,
is a judgement nobody has made yet.

### 2. Test something in-game

Still the largest unknown in the whole project. Every claim made so far
is about file fidelity — byte-identical re-saves, geometry that survives
a round trip. **Nothing has ever been loaded by a Total War executable.**
A single exported unit that renders correctly in Warhammer 3 would
retire more risk than any amount of further corpus work.

### 3. Merge the branch - done

Merged at 1.20.0. `pre-rome2-formats-and-all-versions` fast-forwarded
into `main` - twenty commits, everything from version 1.13.0 onwards:
the pre-Rome 2 formats and writing every version, then Rome 2, Attila,
Warhammer, Warhammer 3 and Pharaoh Dynasties, then the corpus tooling
and the pass that made the panels follow the file format.

The branch name had long outlived its meaning - it was called *pre-Rome
2* and carried six games after it. Work continues on `main`.

## Investigations

### 3b. What a good LOD ladder looks like, per game

The auto-LOD override list still opens on four rows for every version,
and `export_rmv2.default_camera_distance` still has to invent the
distances after the ones the sample files show.

What the 36 sample files do say is that the ladder length is a property
of the *model*, not the game: `maple_d` (Rome 2, v6) has four levels,
`pig` (Three Kingdoms, v8) has two, and most single-mesh props have one.
So the four rows are a decimator default, not a format limit, and
nothing is currently wrong. What they also say is that the *shape*
splits at v7, which is now implemented: before v7 the ladder is
100/200/400 with 500 on a fourth level (`maple_d`, `shrubc_b`,
`grass_rome_atlantic_agri`, `terrain_tile_farmland`); from v7 the last
level is a cutoff in the thousands instead (`chs_warhorse_lowlod`
180/10000, `belt_fabric_03` 10/20/30/10000,
`wh_ksl_birchtree_totem_f` 500/600/5000).

Thirty-six files is not a corpus. What would settle it: sweep each
game's packs for LOD count, camera distance and quality level per RMV2
version, split by model kind (unit, building, vegetation, prop), and see
whether a per-game or per-kind default falls out that beats one flat set
of four rows. Same question for `.variant_part_mesh`, whose parts are
its ladder. Until then the defaults are a starting point the user is
expected to edit, which the panel says.

### 3c. Parent matrix index — answered for Warhammer 3

**Solved there, and written up in FORMATS.md**: it is the bone a
cloth-physics collider proxy follows. All 23 618 vanilla Warhammer 3
models were swept (packs from the game's own `manifest.txt`, extracted
with RPFM, no parse failures); 81 meshes set it, every one a
`collider_*` mesh in one of six `*_cloth_cloak_01` files, and the 58
named after a body part all name exactly the bone their value points
at. Their own matrix index is `-1`, which kills the "parent of the bone
this mesh rides" reading.

The panel offers it again as **Collider Bone**.

What is left: the same sweep on the other games. Warhammer 3 is one
engine generation; whether Rome 2 and Attila used the field the same
way, or at all, is unchecked - the 36-file sample set has it at `-1`
everywhere, which is consistent with "only cloth cloaks use it" but
proves nothing on its own. Three Kingdoms is installed and would be the
cheapest next one.

The tooling is now in [tools/](../tools/), and corpus work goes through
RPFM's CLI rather than a pack reader of our own - see
[tools/README.md](../tools/README.md) for why, and for the two ways a
sweep silently produced wrong numbers before that rule and the vanilla
filter were in place. Re-running this one is:

```
python tools/sweep_field.py parent_matrix_index three_kingdoms "<install>"
```

### 4. The LOD-header marker bytes

`rmv2_format.EXPORT_SIGNATURE` stamps `'R', 'b', 0x00` over three bytes
in every LOD header on export. What is known:

- AssetEditor quotes CA's own developer giving the original C struct:
  `quality_level` is a lone `uint8_t` with nothing after it. The struct's
  other members are 4-byte, so the compiler pads it out — the three
  bytes are alignment, not a field.
- Vanilla files back that up: `gen_tree_oak_large_03` repeats the same
  stale triple (157, 55, 149) across all three LODs while the quality
  byte in front of it varies 2/0/0. That is the signature of
  uninitialised memory, not data.
- RPFM reads all four bytes as one `u32` and casts to `i32`, so a
  non-zero triple can go negative and get clamped to 0 — which is why
  the signature's last byte is 0, keeping the value positive.

What would actually settle it, and has not been done: sweep the WH3 and
Three Kingdoms corpora and ask whether the triple is ever *load-bearing*
— does it correlate with LOD index, mesh count, quality, or the shader
name? Do the values repeat within a file (memory reuse) or across files
from one pack (a build-tool constant)? Is there a subset where it is
reliably zero? If it is junk everywhere, the current stamp is safe and
the question is closed; if any correlation shows up, the stamp is
overwriting something and should go.

### 5. Textures and materials

The shader graph is already more than a passthrough — Principled BSDF,
the TW "orange" normal decode as a node group, material-map channel
separation, gloss inversion, player-colour pickers, alpha modes. What
has never been reviewed is whether it is *right*:

- Does the material map's channel split match what the game's shader
  does with it (metal / AO / mask, and in which order)?
- Gloss → roughness: currently an inversion. Is it linear, or does the
  game apply a curve?
- Are the four `DEFAULT_TEXTURES` placeholders still the right paths for
  the Warhammer 3 era?
- Decal, dirt and emissive slots: read and written, but not wired into
  the graph.

A visual comparison against the same unit in AssetEditor's viewer would
answer most of this quickly.

## The rest of the series

Everything in the series has now been looked at except Troy and Thrones
of Britannia (A and D below). Games come and go off this disk as each
one is swept, so most of the entries below are histories rather than
things that can be re-measured: only Warhammer 3 and Pharaoh Dynasties
are still installed.

### A. Troy - the last one not looked at

Pharaoh is done (G below), which leaves Troy. Expect nothing new: the
several thousand Troy-era meshes Pharaoh carries are all RMV2 v7, so
whatever Troy is, it is almost certainly not a version this add-on has
not already read. The value is confirmation.

Both of the guesses this entry used to make about Pharaoh were wrong,
which is the reason to keep it cheap and census first:

- It was going to ship **PFH6** packs. It ships PFH5, with two
  assembly-kit `_.pack` files in PFH6 holding one XML each.
- **RMV2 v8** was going to be its era. It has none.

### B. Rome 2 - done, and what it turned up

Swept 2026-08-23. Rome 2 was not the formality this entry expected. It
shipped with **RMV2 v5** and **`.anim` v4**, switched to **v6** and
**`.anim` v5** during its own run, and kept **RMV2 v3** - Shogun 2's
layout - for its 3D interface models and some vegetation. Three of
those five had never been read from a real file.

What landed:

- **RMV2 v5 reads and writes properly.** Its every fixed string field is
  UTF-16 at twice the width - a 256-byte skeleton name, 64-byte model
  names, 512-byte texture paths, 116-byte attachment points, a 112-byte
  common header. The v5 write support added in 1.13.0 had been tested
  only by re-versioning a v6 file, so it agreed with a reader that was
  wrong about the whole layout. All 295 vanilla v5 files now parse or
  fail on a material, none silently.
- **RMV2 v3** is Shogun 2's file layout with v2's material shape; it
  needed only the version added to the family. 54 of 54 re-save.
- **`.anim` v4** decoded from scratch: UTF-16 strings, two per-bone
  bitfields where v5 keeps mapping tables, and frames stored the Shogun
  2 way - every bone in every frame, float32 quaternions. 31 of 31.
- **The `.anim` event block** turns out not to be a Shogun 2 exclusive:
  59 Rome 2 cutscene animations close with one (always empty). They had
  been failing as "4 unparsed bytes".
- **Four vertex layouts** decoded: the 60-byte vegetation vertex (trees,
  shrubs, hedges), the 28-byte tree billboard, the 28-byte grass vertex
  whose uvs are float32, and the 12-byte position-and-uv one. All
  import-only, like the other read-only layouts.
- **Two junk-preservation bugs** shared with every era: the
  custom-terrain texture path and the per-mesh shader name both keep
  whatever was in memory after their terminator, and both were being
  zero-padded on write. 163 terrain tiles and 33 trees stopped
  re-saving byte-identically the moment those fields were decoded
  properly - they had been passing for the wrong reason.

Rome 2 now stands at **6012 / 6012 `.anim`** and **10 815 / 14 673
`.rigid_model_v2`**.

### C. Attila - done

Swept 2026-08-23, right behind Rome 2, and it is the same game
format-wise: RMV2 v6 with 208 v5 and 4 v3, `.anim` v5 with the same 31
v4 files carried over. **No `.anim` v6 anywhere**, which was the whole
reason this entry expected Attila to matter - see the note in Known
gaps.

Since it added no versions, the work it paid for was materials, and
that turned out to be the last big class in every era. Now read:
terrain tiles under their Rome 2-era ids (66 with five trailing words,
96 and 97 with six), the projected-decal family (67, 87, 95 - a texture
path and one, nine or ten floats), materials with no header at all (bow
waves, one terrain-tile id), and cloth/rope/collision shapes, which are
a weighted material followed by a simulation block that is kept as read.
Two smaller things fell out with them: LOD headers whose declared vertex
and index totals are zero however much the meshes hold, and mesh
sections that carry further indices after their index block.

Attila stands at **6225 / 6225 `.anim`** and **9971 / 10 015
`.rigid_model_v2`**; the same work took Rome 2 to **14 452 / 14 673**,
and Warhammer 3 - which shares most of those materials - from 253
unreadable meshes to 91 in a 1-in-8 sample, since re-measured in full at
591 of 22 230 (see 7).

### D. Thrones of Britannia - not installed

The last of the middle. Expect Attila's formats exactly; the value is
confirmation, not new support.

### E. Warhammer - done

Swept 2026-08-24.  One mesh version and one animation version - RMV2 v7
and `.anim` v5, with 25 v6 meshes - and **everything reads**: 9335 /
9335 models and 6975 / 6975 animations, byte-for-byte.

Two things in it were new, and both are now supported end to end,
including through Blender:

- **The sway vertex** (vertex format 12, stride 20), under 255 models.
  Two half4s whose W components carry the UV between them, then a
  colour whose alpha is the wind-sway weight.  The position is *not*
  scaled by its W - the only half position in the format that is not -
  and adding the material's pivot to the raw half3 reproduces the
  header's bounding box exactly, which is what settled it.  The normal
  was checked against the face normals of the triangles that use it,
  and the alpha against height: it climbs with height in 95% of the
  game's sway meshes.
- **The interface-banner material** (ids 29 and 30 at 288 bytes), off
  the list in 7 below.

It also refined a field two games had already argued about: 291 of
Warhammer's 6975 animations carry a header word of 0 rather than 1, and
nearly all of them are the animations that ride on a rigid model -
buildings, chariots, war machines - rather than a character rig.  That
is the closest that field has come to having a meaning.

One thing it did *not* have: **`.anim` v6**, still unseen in any vanilla
file.  Warhammer 2 did not have it either (F below), so Troy is the last
candidate.

### F. Warhammer 2 - censused, swept in part

Installed and censused 2026-08-24, then uninstalled while the sweep was
still running, so this entry is honest about what it does and does not
cover.

Nothing radical in the formats, as expected: **RMV2 v7 (17 836) and 36
v6**, **`.anim` v7 (15 230) and v5 (1012)** - every one a version this
add-on already reads and writes.  It is the game that introduced `.anim`
v7, which Warhammer 3 and Three Kingdoms then kept.  **No `.anim` v6**,
which was the whole reason to look: that version is now unseen across
Empire, Napoleon, Shogun 2, Rome 2, Attila, both Warhammers and
Warhammer 3.

The sweep got through 25 684 of the 34 114 files before the packs went
away, and every one re-saved byte-identically: 16 143 animations and
9541 meshes, **no failures of any kind**.  The remaining 8430 were never
read - the uninstall deleted the packs underneath the run - so they are
neither a pass nor a fail.  On the evidence this is a game with no
surprises in it, but "no surprises in three quarters of it" is what was
measured.

Two things it did leave behind, both in the corpus tooling rather than
the add-on:

- **A PFH5 pack is not always zstd.** Warhammer 2 is the first PFH5
  game and compresses with **LZMA1**; Warhammer 3 used LZMA1 too until
  6.2, then zstd and lz4.  Which one a given entry uses is told by the
  payload's own magic, not by the pack version
  (`rpfm_lib/src/compression/mod.rs`).  CA's LZMA1 header is the
  standard one with the decompressed size moved to the front and
  narrowed to a u32, so putting it back where the format wants it - a
  u64 after the properties byte and dictionary size - gives a plain
  LZMA-alone stream that Python's `lzma` reads.  Before this, 3852 of
  Warhammer 2's files looked like unreadable formats when they were
  only compressed differently.
- **The sweep script used to swallow that silently.** An entry that
  failed to extract hit a bare `except: continue`, so it vanished from
  the totals rather than being reported - which is how 8430 files went
  missing from a run that claimed no failures.  It now counts and names
  them.  Worth remembering before PFH6: a file this tooling cannot
  extract must never look like a file it read successfully.

### G. Pharaoh Dynasties - done, and it needed nothing

Swept 2026-08-24, the newest game here and the only one that read
completely on the first attempt with no code written for it:
**13 531 / 13 531 meshes** and **13 505 / 13 505 animations**.

- **RMV2 v7**, with four v6. No v8 anywhere, including in the several
  thousand Troy-era meshes it carries.
- **`.anim` v7**, with 1230 v5 and exactly one v4 - which is Rome 2's
  `camel_v2.anim`, byte for byte, eleven years on. Worth knowing before
  assuming a game's oldest version tells you anything about its era.
- **PFH5 packs**, not the PFH6 this project expected. Only two
  assembly-kit `_.pack` files are PFH6 and they hold one XML each. PFH6
  is handled now anyway: PFH5's header with a 280-byte subheader after
  it, validated the way RPFM suggests - the file index has to end
  exactly at the end of the file.

The 59 meshes that do not read are the encrypted DLC in
`data_special.pack` - 7.99 bits of entropy per byte, all 256 values
present, and the same first bytes across files of different sizes, which
is a fixed keystream over identical header plaintext. Excluded on the
same grounds as Empire's Elite Units DLC; no attempt made to decrypt
them, and none should be.

## Known gaps

### 6. `matrix_index` may double-apply the bone (WH3-era) — unconfirmed

The one flagged as a potential problem and never resolved.
`export_rmv2.extract_mesh_arrays` bakes `obj.matrix_world`'s rotation
and scale into exported vertices. For a mesh bound to a bone by
`skeleton.attach_to_bone` — which forces the Child Of inverse to
identity — `matrix_world` already contains the bone transform, so baking
it again applies the bone twice.

This was **proven and fixed for Shogun 2** (attached models drifted up to
2.3 units; fixed via `bone_local_space`). The modern path is only
*suspected*, and probably has not bitten anyone because destructible
building bones tend to be translation-only, and a translation-only bone
cancels out when just rotation and scale are baked.

Cheap first step: sweep for a vanilla destructible building whose
`matrix_index` bone carries a non-identity rotation. If none exists the
bug is unreachable and can be documented rather than fixed. Do not flip
the modern path to bone-local without that evidence — it is shipped
behaviour, and the modern material's `pivot` field interacts with it.

### 7. The last of the short material headers

The big ones are done (see C above). What is left is a long tail, and
the reason it is left is evidence rather than effort: each of these is
plainly "a name or path, then a run of words", but every vanilla example
has the words at zero, so a layout guess has nothing to be wrong
against.

| id | size (v6 / v5) | files | what |
| --- | --- | --- | --- |
| 45 | 524 / 1036 | ~15 | point lights - two paths and three words, going by how the two versions differ |
| 84 | 1128 | ~7 | Attila's ship night lights |
| 54, 57 | - / 1104, 1168 | 4 | Rome 2 and Attila's greek statues |
| 26 | 80 | 123 models | non_renderable |
| 40 | 544 | 96 models | unidentified, campaign settlements |

Ids 29 and 30 came off this table with Warhammer, which has 19 of them:
the layout was always "a 256-byte name and eight words", and the words
are zero in all 19 as they were in the Rome 2 and Attila ones surveyed
earlier, so they are read as words and written back as they were read.
That is the fallback this entry proposes, applied to the one case where
the name half was certain.  The v5 width (512) follows the rule the
terrain and decal materials set rather than any v5 file, since none has
been seen.

Warhammer 3's `tree_billboard_material` came off this list the same
day, and it was never a material at all: those 591 meshes lay their
section out as material, index block, vertices, so reading the offsets
in the usual order made the material look 36 to 60 bytes too long. See
`RmvModel.indices_first`. Warhammer 3 is now complete, 22 230 of 22 230
meshes.

One loose end from the same table: Rome 2's single debug-geometry mesh
at stride 20 with no material header *should* now resolve, since a
format-less mesh at that stride reads as the sway vertex - but Rome 2 is
off the disk, so that is reasoning, not a measurement.

A file with a non-zero example of any of these would settle it. Failing
that, the honest fallback is to keep an unknown material's bytes
verbatim the way the cloth block is kept - it would clear the whole tail
at once, at the cost of not knowing what is in them.

### 8. Vertex formats that read but do not write - done

Every layout this add-on reads, it now writes, and every one of them
survives a Blender round trip. The channels that have no ordinary mesh
field - a vegetation vertex's rest position and eight wind weights, a
bow wave's second position, custom terrain's two spare colour channels -
live in `RmvMeshData.extras` and travel through Blender as point
attributes (`rmv2_pivot`, `rmv2_wind_0`, ...).

Warhammer's sway vertex joined them and needed none of that: everything
it stores lands on an ordinary mesh field.  Adding it did turn up two
bugs that had been there since the vegetation work, though.  A layout
that carries vertex colour but was not on a hand-written list - Shogun
2's static vertex, and the sway one - lost its colours through Blender;
the check now asks the layout's own dtype instead.  And a mesh built in
Blender declared this module's private format id rather than the one CA
writes, so an exported tree, grass patch or water plane said 104, 106 or
100 where the game reads 6, 5 or 8.  See `declared_format_id`.

What is left of this entry is a question rather than a gap: the wind
weights are eight halves whose meaning is unknown, and five of the eight
are constant per mesh in every vanilla file looked at. If they were ever
identified they could be presented as something better than eight
numbers - a stiffness, a phase, an amplitude.

### 9. Smaller things

- **v8 export uses the uncompressed rates**, so exported animations are
  two to three times CA's size. Correct and lossless, but a range-fitting
  encoder would match vanilla. Optional.
- **Multi-part v8 collapses to one part** through Blender: the importer
  concatenates parts into one timeline, so a re-export writes a single
  part. Plays the same; differs structurally.
- **Empire's 224 Elite Units DLC meshes** are encrypted (ECB, fixed key).
  Out of scope deliberately.
- **`.rigid_model_v2` v0** — 4 Shogun 2 files, no skeleton-name field and
  meshes beyond what its LOD table declares. Refused cleanly.
- **`.anim` v6 is still unread from a vanilla file**, and Attila was the
  last good guess. Warhammer 1 and 2 are what is left to check.
- **Empire, Napoleon and Shogun 2 are uninstalled**, so `samples/` is now
  the only local copy of their reference files. Any further corpus sweep
  for those games needs a reinstall.
- **`samples/` should grow** as each game above is checked - a couple of
  files per format per game, the way Empire, Shogun 2, Warhammer 3,
  Three Kingdoms and now Rome 2 are covered. It is gitignored, so it is
  only ever as good as the local copy.
- **Rome 2's v3 export path is untested in the game.** v3 goes out
  through the Shogun 2 writer, which puts vertices in bone space. That
  is right for Shogun 2 and merely consistent for v3; no v3 file has
  been round-tripped through Blender and loaded by Rome 2.

### 10. RMV2's own parameter lists have no UI

The named parameter blocks of `.animatable_rigid_model`,
`.variant_part_mesh` and `.variant_weighted_mesh` are now an editable
list in the panel each container puts them in (see
[FORMATS.md](FORMATS.md#the-named-parameter-block)).
`.rigid_model_v2` is the one left out, and for a reason: it addresses
its parameters by *index* rather than by name, and only five indices
have a known meaning — float 0 and 1 are the UV scale, int 0 is the
alpha mode (already its own field), int 1 decal, int 2 dirt, vec4 0 the
decal transform. The rest are read and written back unchanged inside
`extra_json`.

Surfacing them means naming the indices, and naming an index without
knowing what it does is how "parent matrix index" got mis-described in
the first place (3c above). The cheap half is worth doing on its own:
float 0/1 are the UV scale, which is a real thing to want to edit, and
they could be a **UV Scale** field on the mesh panel without touching
the unknown ones. The rest wants a corpus sweep of which indices
actually occur, per game, before any of them gets a label.

### 11. Which vertex formats go with which version

The Vertex Format dropdown is narrowed by **container** — an RMV2 mesh
sees the RMV2 layouts, a `.variant_part_mesh` mesh sees only its own
three — and deliberately not by version, because nothing yet says what a
version-level rule would be. The samples actively argue against the
obvious guess:

| File | Version | Format |
| --- | --- | --- |
| `zen_garden_floor_stone_01` | v1 | `Static` |
| `compoundwall_v2_wood_low_endcap` | v2 | `Static` |
| `shrub_pine_a` | v3 | `Vegetation` |
| `army_banner_flag_general` | v7 | `Shogun2_Static` |

So the `S2_*` entries are not a Shogun-2-era set and the modern ones are
not a modern-era set — each appears on the wrong side. That is why an
RMV2 v8 model still lists the Shogun 2 layouts today: not because it is
right, but because there is no evidence yet for where to draw the line.

**What is needed** is a per-game census: for every vanilla
`.rigid_model_v2`, the (version, vertex format) pair, counted. That gives
the real table, and any pair with a healthy count is a pair the dropdown
should offer at that version. A pair that never occurs is a candidate for
hiding — though "never occurs in this game" is weaker than "the format
cannot hold it", so the honest outcome may be sorting the list with the
unlikely ones last rather than removing them.

Warhammer 3 is the only game currently installed, and it only covers v7
and v8. The interesting versions are the old ones, so this wants Shogun 2
(v1–v3), Rome 2 (v5/v6) and Attila back on the disk — see
[CORPUS.md](CORPUS.md), which notes that games come and go as each is
swept.

The tooling is already there: `tools/corpus.py` iterates a game's vanilla
models, and this is a two-counter loop over `model.version` and
`entry.material.vertex_format`. Worth adding to `tools/` as a real script
once more than one game is available to run it against.

Related: the same sweep answers whether a *file* mixes formats, which it
does — `belt_fabric_03` is Cinematic plus Weighted, `maple_d` is
Vegetation plus one Tree Billboard card, and Warhammer 3 routinely drops
a Cinematic mesh to Weighted for its distant LOD. That is settled and is
why the setting is per mesh.
