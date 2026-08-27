"""Count what a .rigid_model_v2 material field actually holds, per game.

This is the shape most of docs/TODO.md's open questions have: "the file
has a field, our sample set says one thing, does the whole game agree?"

    python tools/sweep_field.py parent_matrix_index warhammer_3 "D:\\...\\Total War WARHAMMER III"

It prints a histogram of the field's values over every vanilla mesh, and
lists every mesh that is not at the common value along with its model
name and skeleton - which is usually what identifies the field. That is
how `parent_matrix_index` was pinned down: all 81 outliers in Warhammer 3
turned out to be meshes named `collider_*`, and the 58 named after a body
part each named exactly the bone their value pointed at.

Pack access is RPFM's (see corpus.py). Nothing here reads a pack itself.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus  # noqa: E402

corpus.add_addon_to_path()
import rmv2_format as rf  # noqa: E402


def sweep(field, game_key, game_dir, out_path=None, vanilla_only=True):
    c = corpus.Corpus(game_key, game_dir)
    values = collections.Counter()
    versions = collections.Counter()
    outliers = []
    parsed = 0
    failures = collections.Counter()

    for path, data in c.iter_files(".rigid_model_v2",
                                   vanilla_only=vanilla_only):
        try:
            model = rf.load(data)
        except Exception as exc:          # noqa: BLE001 - report, not raise
            failures[type(exc).__name__] += 1
            continue
        parsed += 1
        versions[model.version] += 1
        for lod in model.lods:
            for entry in lod.models:
                material = entry.material
                if not hasattr(material, field):
                    continue
                value = getattr(material, field)
                values[value] += 1
                outliers.append((value, path, model.version,
                                 material.model_name, model.skeleton_name,
                                 material.matrix_index))

    if not values:
        print("no mesh carried %r" % field)
        return 1

    common, _ = values.most_common(1)[0]
    odd = [row for row in outliers if row[0] != common]

    report = []
    report.append("game: %s" % game_key)
    report.append("models parsed: %d" % parsed)
    report.append("by rmv2 version: %s" % dict(sorted(versions.items())))
    report.append("%s values: %s" % (field, dict(sorted(values.items()))))
    report.append("parse failures: %s" % dict(failures))
    report.append("")
    report.append("commonest value %r covers %d of %d meshes"
                  % (common, values[common], sum(values.values())))
    report.append("")
    report.append("meshes not at %r (%d):" % (common, len(odd)))
    for value, path, version, mesh, skeleton, matrix_index in odd:
        report.append("%s=%s|path=%s|v%d|mesh=%s|skeleton=%s|"
                      "matrix_index=%s"
                      % (field, value, path, version, mesh, skeleton,
                         matrix_index))
    text = "\n".join(report)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print("wrote", out_path)
    print("\n".join(report[:8]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("field", help="material field, e.g. "
                                      "parent_matrix_index")
    parser.add_argument("game", help="RPFM game key, e.g. warhammer_3")
    parser.add_argument("game_dir", help="the game's install folder")
    parser.add_argument("-o", "--out", help="write the full report here")
    parser.add_argument("--include-mods", action="store_true",
                        help="do not filter to CA's own packs. Off by "
                             "default: mods use fields vanilla never "
                             "does, and they are not evidence about the "
                             "format")
    args = parser.parse_args(argv)
    return sweep(args.field, args.game, args.game_dir, args.out,
                 vanilla_only=not args.include_mods)


if __name__ == "__main__":
    sys.exit(main())
