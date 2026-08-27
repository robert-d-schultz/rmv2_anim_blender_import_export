# Corpus tools

For the open questions in [../docs/TODO.md](../docs/TODO.md), which
mostly take the form *"the file has a field, our sample set says one
thing — does the whole game agree?"*

## The rule: pack access goes through RPFM's CLI

Not a reader of our own. RPFM is the reference implementation for the
container — it handles the compressed and encrypted entries, six header
versions and the per-game quirks — and it is the tool results get checked
in. A second implementation written for one sweep is a second thing to be
wrong, and when the two disagree the sweep stops being evidence.

`corpus.py` shells out to `rpfm_cli.exe` and never parses a `.pack` byte
itself. Point at the binary with `RPFM_CLI` if it isn't on `PATH`, and at
an RPFM source checkout with `RPFM_SRC` (only needed for the older games,
below).

## Two ways a sweep goes quietly wrong

Both of these produced confident, wrong numbers before they were caught,
so `corpus.py` handles them rather than leaving them to each script:

**Mods in the data folder.** They sit beside CA's packs, set the same
pack-type word — a workshop pack claiming `Release` is common — and they
*do* use fields vanilla never touches. Including them turned 81 real
`parent_matrix_index` hits in Warhammer 3 into 1021. The filter is RPFM's
own rule: a game with `data/manifest.txt` is filtered by it, and the
older games fall back to the hardcoded list in RPFM's
`supported_games.rs`, read from that source so there is one source of
truth. If neither is available `corpus.py` raises rather than sweeping
something it cannot vouch for.

**Windows MAX_PATH.** Extracted game trees run past 260 characters, and
`open()` then fails on files `os.walk` lists happily. A sweep that
catches those in an `except` undercounts — a third of the hits, in the
case that found this — and looks healthy. Extraction goes to a short work
dir, `open_binary` prefixes every path, and `iter_files` warns loudly
when a pack extracts fewer files than it listed.

## Usage

### Byte-identity, the central invariant

```
python tools/sweep_roundtrip.py warhammer_3 "D:\...\Total War WARHAMMER III"
```

Reads every vanilla file of every supported format, writes it back, and
reports anything that is not byte-identical, with offsets. This is the
sweep behind [../docs/CORPUS.md](../docs/CORPUS.md). `--formats` narrows
it to one extension and `--limit` stops early for a quick check. The one
licensed difference is `.rigid_model_v2`'s export signature in the v7/v8
LOD padding; everything else that moves is a failure.

### What a field actually holds

```
python tools/sweep_field.py parent_matrix_index warhammer_3 "D:\...\Total War WARHAMMER III" -o report.txt
```

Prints a histogram of a material field over every vanilla mesh, and lists
every mesh away from the common value with its model name and skeleton —
which is usually what identifies the field. `parent_matrix_index` was
pinned down exactly this way: all 81 outliers were meshes named
`collider_*`, and the 58 named after a body part each named exactly the
bone their value pointed at. See
[../docs/FORMATS.md](../docs/FORMATS.md).

`--include-mods` turns the vanilla filter off, which is occasionally
useful for seeing what modders do with a field. It is not evidence about
the format.

For anything more than a field histogram, import `corpus.Corpus` and
write the loop:

```python
from corpus import Corpus, add_addon_to_path
add_addon_to_path()
import rmv2_format as rf

c = Corpus("warhammer_3", r"D:\...\Total War WARHAMMER III")
for path, data in c.iter_files(".rigid_model_v2"):
    model = rf.load(data)
    ...
```

The format modules (`rmv2_format`, `anim_format`, `arm_format`,
`vmpf_format`, `vwm_format`) are bpy-free, so this needs no Blender.

## Disk

`iter_files` extracts one pack at a time and wipes the work dir between
them, so peak usage is the largest pack rather than the whole game.
Warhammer 3's models are about 900 MB at the worst pack.
