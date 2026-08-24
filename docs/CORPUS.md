# Corpus results

Re-saving an unmodified file byte-for-byte is a tested invariant, not an
aspiration. Every game that has been on this disk was swept in full: read
every vanilla file of every supported format, write it back, and require
the bytes to match. The only difference allowed is `EXPORT_SIGNATURE` in
v7/v8 LOD padding.

Numbers below are as measured, on the date they were measured. Games come
and go off the disk as each is swept, so most of them can no longer be
re-run — only `samples/` remains. For what each game turned up in detail,
see [TODO.md](TODO.md).

## Byte-identical re-saves

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
| Attila `.anim` | 6225 / 6225 — every one |
| Attila `.rigid_model_v2` | 9971 / 10 015 |
| Warhammer `.anim` | 6975 / 6975 — every one |
| Warhammer `.rigid_model_v2` | 9335 / 9335 — every one |
| Warhammer 2 `.anim` | 16 143 / 16 143 — of the three quarters swept, see below |
| Warhammer 2 `.rigid_model_v2` | 9541 / 9541 — likewise |
| Warhammer 3 `.anim` | 34 997 / 34 997 — every one, v5, v7 and v8 |
| Warhammer 3 `.rigid_model_v2` | 22 230 / 22 230 — every one |
| Pharaoh Dynasties `.anim` | 13 505 / 13 505 — every one, v4, v5 and v7 |
| Pharaoh Dynasties `.rigid_model_v2` | 13 531 / 13 531 — every one that is not encrypted DLC |

**Warhammer, Warhammer 3 and Pharaoh Dynasties read completely.** So do
Empire and Napoleon, in every supported format, with one exclusion:
Empire's 224 Elite Units DLC `.variant_weighted_mesh` files, which ship
under a per-file cipher. Napoleon's are not encrypted.

CA's own `testdata/` folder is excluded from the `.anim` counts — it
holds half-finished exports from formats that never shipped — though the
rigid models and weighted meshes in it do round-trip.

Two counts are older than the interface-banner material and are quoted as
they were measured: the 44 unreadable files in Attila and 221 in Rome 2
included their banners, and so do the Rome 2 and Attila rows above. Both
games have since come off this disk, so the honest thing is to leave the
numbers as they were taken rather than adjust them by arithmetic.

## Per game

**Rome 2** turned out to be two games in one, shipping with RMV2 v5 and
`.anim` v4 (UTF-16 strings throughout) before switching mid-life to v6
and v5. Three of its five versions had never been read from a real file.
See [Format notes](FORMATS.md#rome-2-is-two-eras-in-one-game).

**Attila** is the same game format-wise, down to shipping Rome 2's
animation files unchanged, so what it was good for was **materials** —
terrain tiles, the projected-decal family, headerless materials, and
cloth/rope/collision blocks. That work also moved Rome 2 and Warhammer 3,
which share most of those materials.

**Warhammer** is a quiet game — one mesh version and one animation
version — and it contributed two things: the
[sway vertex](FORMATS.md#warhammers-sway-vertex) under 255 of its models,
and the interface-banner material, which was the largest entry left on
the short-material-header list.

**Warhammer 2** was installed long enough to be censused in full and
three quarters swept before it came off the disk again. It holds no
version this add-on did not already read, and it is where `.anim` v7
starts. Of the 25,684 of 34,114 files the sweep reached, every one
re-saved byte-identically with no failure of any kind; the rest were
never read, because the uninstall took the packs out from under the run,
so they are **neither a pass nor a fail**.

**Warhammer 3** was completed by the tree billboard — 591 meshes, every
failure the game had left, and not a material problem at all. See
[Format notes](FORMATS.md#tree-billboards-put-their-indices-first).

**Pharaoh Dynasties** is the newest game here and the only one that
needed no work: every file it ships read and re-saved on the first
attempt. Its `.anim` v4 file is Rome 2's `camel_v2.anim`, shipped byte
for byte eleven years later — worth knowing before assuming a game's
oldest version number tells you anything about its era.

It also corrected two things this project had written down without
checking. Its packs are **PFH5**, not the PFH6 expected of it (only two
assembly-kit `_.pack` files are PFH6, and those hold one XML each). And
it has **no RMV2 v8 anywhere**, including in the several thousand
Troy-era meshes it carries, which are all v7 — so v8 belongs to Warhammer
3 and Three Kingdoms, and the "Troy era" attribution this add-on used to
make was never founded on anything.

**`.anim` v6 exists in no vanilla file of any game swept** — Empire,
Napoleon, Shogun 2, Rome 2, Attila, both Warhammers, Warhammer 3 and
Pharaoh. Troy is the last candidate.

## Exclusions

Two sets of files are excluded deliberately rather than because they
cannot be parsed:

- **Empire's 224 Elite Units DLC** `.variant_weighted_mesh` files (ECB,
  fixed key).
- **Pharaoh's 59 meshes in `data_special.pack`** — 7.99 bits of entropy
  per byte, all 256 byte values present, and identical leading bytes
  across files of different sizes, which is a fixed keystream over
  identical header plaintext.

Both are CA's DLC protection. No decryptor has been written for either,
and none should be.

## About the tooling

Every number above comes from a corpus sweep script, not from the add-on
itself — the add-on reads loose files. Two things about that script are
worth recording:

**Pack compression is told by the payload, not the pack version.** A
compressed PFH5 entry is not necessarily zstd: Warhammer 2 uses **LZMA1**,
and so did Warhammer 3 until its 6.2 patch. Which one a given entry uses
is decided by the payload's own magic
(`rpfm_lib/src/compression/mod.rs`). CA's LZMA1 header is the standard
one with the decompressed size moved to the front and narrowed to a u32.
Before this was understood, 3852 of Warhammer 2's files looked like
unreadable formats when they were only compressed differently.

**A file the tooling cannot extract must never look like a file it read
successfully.** An entry that failed to extract used to hit a bare
`except: continue`, so it vanished from the totals rather than being
reported — which is how 8430 Warhammer 2 files went missing from a run
that claimed no failures, caught only because the sweep total disagreed
with the census. It now counts and names them.
