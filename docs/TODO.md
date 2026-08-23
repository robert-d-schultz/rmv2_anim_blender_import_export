# Where this stands, and what's left

Written 2026-08-23, after version 1.13.0 (every format version now
writes). Roughly in priority order within each group.

## Needs a human

### 1. Manual pass in Blender, and the UI that came with it

Nothing here has been driven by hand since the version work landed — it
is all verified through headless round-trips, which check bytes, not
whether the panels make sense.

New UI to look at:

- **Five version pickers**, one per exporter: RMV2 (now includes v5),
  `.anim` (now includes v8), `.animatable_rigid_model` (v0–v5),
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

### 3. Commit

32 changed or untracked files, ~5,700 insertions since `76d4e28`. That
is the entire Shogun 2, Empire/Napoleon, `.variant_weighted_mesh`,
`.rigid_model_animation` and version-writing effort, unversioned. The
`samples/` folder is gitignored and stays local.

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

### 7. Modern meshes this add-on still cannot read

Present in both Warhammer 3 and Three Kingdoms, so era-wide rather than
game-specific:

- decal / UI / terrain-tile meshes that truncate mid-parse (the largest
  group by far)
- `CustomTerrain` at stride 60 — tree and vegetation meshes
- `Position16_bit` at stride 28 — grass
- one cloth material whose header size does not match
- vertex format id **12**, which is not in the known enum at all

### 8. Vertex formats that read but do not write

`Collision`, `Position16` and the two custom-terrain layouts, matching
AssetEditor. Related to 7 but a separate job: 7 is about parsing files at
all, this is about writing layouts already understood.

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
