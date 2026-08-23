# Where this stands, and what's left

Written 2026-08-23, after version 1.14.0 (Rome 2 read end to end; every
format version writes). Roughly in priority order within each group.

## Needs a human

### 1. Manual pass in Blender, and the UI that came with it

Nothing here has been driven by hand since the version work landed — it
is all verified through headless round-trips, which check bytes, not
whether the panels make sense.

New UI to look at:

- **Five version pickers**, one per exporter: RMV2 (v1, v2, v3, v5–v8),
  `.anim` (v0, v1, v4–v8), `.animatable_rigid_model` (v0–v5),
  `.variant_part_mesh` (v0/v2/v3), `.variant_weighted_mesh`
  (v1/headerless).
- **"Other Formats" box** on the RMV2 collection panel, showing
  `arm_version`, `vmpf_version` and `vwm_version` together.
- **Header Type** row on the armature panel (the `.anim` header word).

Expected outcome, as you predicted: most of this should be hidden unless
it applies. A collection imported from a `.variant_part_mesh` has no use
for `vwm_version`. The obvious rule is to show only the version for the
format the model came from, with the rest behind a toggle — but which
formats a given model can *sensibly* be written as is a judgement call,
so worth deciding at the panel rather than guessing here.

### 2. Test something in-game

Still the largest unknown in the whole project. Every claim made so far
is about file fidelity — byte-identical re-saves, geometry that survives
a round trip. **Nothing has ever been loaded by a Total War executable.**
A single exported unit that renders correctly in Warhammer 3 would
retire more risk than any amount of further corpus work.

### 3. Merge the branch

Everything since `76d4e28` sits on `pre-rome2-formats-and-all-versions`,
not on `main`: the Shogun 2, Empire/Napoleon,
`.variant_weighted_mesh`, `.rigid_model_animation`, version-writing and
Rome 2 work. Fast-forwarding `main` onto it is a one-liner and your
call, not this add-on's:

    git checkout main && git merge --ff-only pre-rome2-formats-and-all-versions

## Investigations

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

Rome 2 is installed and swept (B below). Attila and Warhammer 2 are
empty shells, Pharaoh has a single mod pack, and Troy and Thrones of
Britannia are not on disk at all, so those still start with an install.

### A. Troy and Pharaoh - forward from Warhammer 3

The two newest, and the two most likely to have moved on:

- **Pharaoh ships PFH6 packs.** Every reader here handles PFH0 through
  PFH5; 6 is unread. RPFM has `pfh6.rs` to work from. Expect the same
  per-entry compression flag PFH5 uses, but that is an assumption.
- Troy is the era the README already attributes RMV2 v8 to ("Warhammer
  3 / Troy era"), which has never actually been checked against a Troy
  file.
- Both are worth a version and vertex-format census before anything
  else: new `TextureType` values, new material types, and new vertex
  layouts are exactly what a later game adds, and all three would show
  up immediately in the survey that Three Kingdoms went through.

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

### C. Attila and Thrones of Britannia - next

Attila is the obvious next install: it is the likely source of
**`.anim` v6**, which has still never been read from a real file
(Warhammer 3's are v8, v7 and v5; Three Kingdoms' are all v7; Rome 2's
are v5 and v4). Expect Rome 2's v6 mesh layout to carry over - and
expect the material gap below to be the limit there too.

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

### 7. Materials with short headers of their own - the one real gap left

Rome 2 made the shape of this clear. Every remaining unreadable mesh,
in every era, fails the same way: the material id is not one of the
three this add-on knows (weighted, custom terrain, terrain tiles), so
the weighted layout is tried and reads off the end of a much shorter
header. It is a material problem, not a vertex problem.

The sizes are fixed per id and per era, which is what makes them
tractable. From Rome 2:

| id | v6 size | v5 size | what |
| --- | --- | --- | --- |
| 22 (bow_wave) | 0 | - | no material at all |
| 48 (terrain tile) | 0 | - | no material at all |
| 26 (non_renderable) | 80 | - | |
| 66 (terrain tiles) | 84 | 148 | our struct is 88: Rome 2 has five trailing u32, Warhammer six |
| 29 / 30 (texture_combo) | 288 | - | the 3D interface banners |
| 67 (projected_decal) | 260 | 516 | a texture path plus four bytes |
| 87 (projected_decal_v2) | 292 | - | a texture path plus a vec3 pair |
| 40 | 544 | - | |
| 45 (point light) | 524 | 1036 | |
| 60 (cloth) / 62 (collision_shape) | varies | varies | |

Warhammer 3 has the same classes plus `tree_billboard_material`, and
one vertex format id, **12**, that is in no enum here.

Two of them are free: a size of 0 means the material is absent, and the
vertex format then has to come from the stride the way the Shogun 2
path already does it.

### 8. Vertex formats that read but do not write

`Collision`, `Position16`, the two custom-terrain layouts, and the four
Rome 2 vegetation ones (vegetation, tree billboard, grass, position and
uv). Related to 7 but a separate job: 7 is about parsing files at all,
this is about writing layouts already understood. Three of the four new
ones carry per-vertex fields `RmvMeshData` has nowhere to put - a rest
position and eight halves of wind sway - so they would need somewhere
to live before a mesh could be rebuilt from Blender.

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
