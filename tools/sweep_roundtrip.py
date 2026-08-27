"""Read every vanilla file of a format in a game, re-save, compare bytes.

The sweep behind [docs/CORPUS.md](../docs/CORPUS.md). Re-saving an
unmodified file byte-for-byte is this project's central invariant, and
the only way to hold the reader and writer to CA's idea of a format
rather than to each other's.

    python tools/sweep_roundtrip.py warhammer_3 "D:\\...\\Total War WARHAMMER III"
    python tools/sweep_roundtrip.py shogun_2 "<install>" --formats .variant_part_mesh

Pack access is RPFM's (see corpus.py). One licensed difference:
`.rigid_model_v2` stamps `rmv2_format.EXPORT_SIGNATURE` over the three
padding bytes of each v7/v8 LOD header, so exported files can be told
from CA's. Anything else that moves is a failure and gets reported with
its offsets.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus  # noqa: E402

corpus.add_addon_to_path()

import anim_format  # noqa: E402
import arm_format  # noqa: E402
import rmv2_format  # noqa: E402
import vmpf_format  # noqa: E402
import vwm_format  # noqa: E402

FORMATS = {
    ".rigid_model_v2": (rmv2_format.load, rmv2_format.save),
    ".anim": (anim_format.load, anim_format.save),
    ".animatable_rigid_model": (arm_format.load, arm_format.save),
    ".rigid_model": (arm_format.load, arm_format.save),
    ".rigid_model_animation": (arm_format.load, arm_format.save),
    ".variant_part_mesh": (vmpf_format.load, vmpf_format.save),
    ".variant_weighted_mesh": (vwm_format.load, vwm_format.save),
}


def _licensed_difference(suffix, original, rewritten, model):
    """True when the only changed bytes are the export signature."""
    if suffix != ".rigid_model_v2":
        return False
    signature = set(rmv2_format.EXPORT_SIGNATURE)
    differing = [i for i in range(len(original))
                 if original[i] != rewritten[i]]
    if not differing or len(differing) > 3 * len(model.lods):
        return False
    return all(rewritten[i] in signature for i in differing)


def sweep_format(game_corpus, suffix, limit=None, verbose=False):
    load, save = FORMATS[suffix]
    counts = collections.Counter()
    problems = []

    for path, blob in game_corpus.iter_files(suffix):
        counts["seen"] += 1
        try:
            model = load(blob)
        except Exception as exc:                       # noqa: BLE001
            counts["unreadable"] += 1
            problems.append((path, "read: %s: %s"
                             % (type(exc).__name__, exc)))
            continue
        try:
            out = save(model)
        except Exception as exc:                       # noqa: BLE001
            counts["unwritable"] += 1
            problems.append((path, "write: %s: %s"
                             % (type(exc).__name__, exc)))
            continue

        if out == blob:
            counts["identical"] += 1
        elif len(out) == len(blob) and _licensed_difference(
                suffix, blob, out, model):
            counts["signature only"] += 1
        else:
            counts["DIFFERENT"] += 1
            if len(out) != len(blob):
                why = "length %d -> %d" % (len(blob), len(out))
            else:
                diff = [i for i in range(len(blob)) if blob[i] != out[i]]
                why = "%d byte(s) at %s" % (len(diff), diff[:8])
            problems.append((path, why))
        if limit and counts["seen"] >= limit:
            break
        if verbose and counts["seen"] % 500 == 0:
            print("  %s: %d seen, %d not clean"
                  % (suffix, counts["seen"], counts["DIFFERENT"]),
                  file=sys.stderr, flush=True)
    return counts, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("game", help="RPFM game key, e.g. warhammer_3")
    parser.add_argument("game_dir", help="the game's install folder")
    parser.add_argument("--formats", nargs="+", default=None,
                        help="which extensions to sweep (default: all "
                             "this add-on reads)")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many files per format, for "
                             "a quick check")
    parser.add_argument("-o", "--out", help="write the full report here")
    args = parser.parse_args(argv)

    suffixes = args.formats or list(FORMATS)
    unknown = [s for s in suffixes if s not in FORMATS]
    if unknown:
        parser.error("unknown format(s): %s" % ", ".join(unknown))

    game_corpus = corpus.Corpus(args.game, args.game_dir)
    report = ["game: %s" % args.game, ""]
    clean = True

    for suffix in suffixes:
        counts, problems = sweep_format(game_corpus, suffix,
                                        limit=args.limit, verbose=True)
        if not counts["seen"]:
            continue
        good = counts["identical"] + counts["signature only"]
        report.append("%-26s %d / %d re-saved byte-identically%s"
                      % (suffix, good, counts["seen"],
                         "" if not counts["signature only"] else
                         " (%d with the export signature)"
                         % counts["signature only"]))
        for key in ("unreadable", "unwritable", "DIFFERENT"):
            if counts[key]:
                clean = False
                report.append("  %-12s %d" % (key, counts[key]))
        for path, why in problems[:40]:
            report.append("    %s: %s" % (path, why))
        if len(problems) > 40:
            report.append("    ... and %d more" % (len(problems) - 40))

    text = "\n".join(report)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print("\nwrote", args.out)
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
