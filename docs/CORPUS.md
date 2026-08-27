# Corpus results

Re-saving an unmodified file byte-for-byte is a tested invariant. Every
game listed here has been swept in full: read every vanilla file of every
supported format, write it back, and require the bytes to match. The only
difference allowed is `EXPORT_SIGNATURE` in v7/v8 LOD padding.

Numbers are as measured, on the date they were measured. Games come and
go off the disk as each is swept, so most cannot be re-run.

**How to run one.** `python tools/sweep_roundtrip.py <game> "<install>"`.
It reads every vanilla file of every format this add-on supports, writes
it back, and reports anything that is not byte-identical with its
offsets. Pack access goes through RPFM's CLI rather than a reader of our
own, and CA's packs are told from mods by the game's own manifest - see
[tools/README.md](../tools/README.md), which also records the two ways a
sweep produced confident wrong numbers before those rules were in place.

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
| Rome 2 `.anim` | 6012 / 6012 |
| Rome 2 `.rigid_model_v2` | 14 452 / 14 673 |
| Attila `.anim` | 6225 / 6225 |
| Attila `.rigid_model_v2` | 9971 / 10 015 |
| Warhammer `.anim` | 6975 / 6975 |
| Warhammer `.rigid_model_v2` | 9335 / 9335 |
| Warhammer 2 `.anim` | 16 143 / 16 143 — of the portion swept, see below |
| Warhammer 2 `.rigid_model_v2` | 9541 / 9541 — likewise |
| Warhammer 3 `.anim` | 34 997 / 34 997 |
| Warhammer 3 `.rigid_model_v2` | 22 230 / 22 230 |
| Pharaoh Dynasties `.anim` | 13 505 / 13 505 |
| Pharaoh Dynasties `.rigid_model_v2` | 13 531 / 13 531 |

**Warhammer, Warhammer 3 and Pharaoh Dynasties read completely.** So do
Empire and Napoleon in every supported format, excluding the encrypted
DLC below.

CA's own `testdata/` folder is excluded from the `.anim` counts: it holds
exports from formats that never shipped. The rigid models and weighted
meshes in it do round-trip.

The Rome 2 and Attila rows predate the interface-banner material and
count its meshes as failures — 221 and 44 unreadable files respectively.
Both games are off the disk, so the numbers stand as taken rather than
being adjusted by arithmetic.

## Versions by game

| Game | `.rigid_model_v2` | `.anim` |
| --- | --- | --- |
| Empire, Napoleon | — | headerless only (3758 and 4027 files) |
| Shogun 2 | v1, v2 | v1, and the headerless variant |
| Rome 2 | v5 (295), v6, v3 (54) | v4 (31), v5 |
| Attila | v6, v5 (208), v3 (4) | v5, v4 (the same 31 files) |
| Warhammer | v7 (9310), v6 (25) | v5 |
| Warhammer 2 | v7 (17 836), v6 (36) | v7 (15 230), v5 (1012) |
| Three Kingdoms | v7, v8 | v7, v8 |
| Warhammer 3 | v7, v8 | v5, v7, v8 |
| Pharaoh Dynasties | v7, v6 (4) | v7, v5 (1230), v4 (1) |

Rome 2 is two eras of format in one game: it shipped with RMV2 v5 and
`.anim` v4, whose fixed strings are UTF-16 at double width, and switched
to v6 and v5 during its own run. Attila is the same game format-wise and
ships Rome 2's animation files unchanged.

Warhammer 2 introduces `.anim` v7. Warhammer 3 and Three Kingdoms are the
only games with RMV2 v8.

Pharaoh's single `.anim` v4 file is Rome 2's `camel_v2.anim`, shipped
byte for byte eleven years later. A game's oldest version number says
nothing about its era.

**`.anim` v6 occurs in no vanilla file** of Empire, Napoleon, Shogun 2,
Rome 2, Attila, either Warhammer, Warhammer 3 or Pharaoh. Troy is the
only game not yet checked for it.

## Coverage notes

**Warhammer 2 is a partial sweep.** 25,684 of its 34,114 files were read
and re-saved, all byte-identically; the game was uninstalled before the
run finished, and the remaining 8430 files were never read. They are
neither a pass nor a fail.

**Thrones of Britannia and Troy have not been swept.** Neither is
expected to add a version: Pharaoh's several thousand Troy-era meshes are
all RMV2 v7.

**Three Kingdoms** was censused for versions but never swept, so it has
no row in the table above.

## Exclusions

Two sets of files are excluded deliberately rather than for want of a
parser. Both are CA's DLC protection:

- **Empire's 224 Elite Units `.variant_weighted_mesh` files** — ECB, a
  fixed key. Napoleon's equivalents are in the clear and work normally.
- **Pharaoh's 59 DLC meshes** — 7.99 bits of entropy per byte, all 256
  byte values present, and identical leading bytes across files of
  different sizes, which is a fixed keystream over identical header
  plaintext.

No decryptor has been written for either, and none should be.
