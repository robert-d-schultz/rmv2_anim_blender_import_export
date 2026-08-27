"""Reading a game's whole corpus, for the investigations in docs/TODO.md.

**Pack access goes through RPFM's CLI, always.** RPFM is the reference
implementation for the container: it handles the compressed and
encrypted entries, the six header versions, and the per-game quirks, and
it is what rob checks results in. A hand-rolled pack reader written for
one sweep is a second implementation to be wrong in a second way - and
when the two disagree the sweep stops being evidence. So this module
shells out, and nothing here parses a .pack byte itself.

What it does own is the two things that made a sweep silently wrong
before:

* **Which packs are CA's.** Mods sit in the same folder, set the same
  pack-type word, and do use fields vanilla never touches - a Warhammer 3
  sweep that includes them reports 1021 hits where the truth is 81. The
  rule is RPFM's own: a game with `data/manifest.txt` is filtered by it,
  and the older games fall back to the hardcoded list in RPFM's
  `supported_games.rs`, read from source so there is one source of truth.

* **Long paths.** Extracted trees run past Windows' 260-character
  MAX_PATH, and `open()` then fails on files `os.walk` happily lists. A
  sweep that swallows those in an `except` undercounts by a third and
  looks fine. `open_binary` prefixes every path, so it cannot recur.

Usage:

    from corpus import Corpus
    c = Corpus("warhammer_3", r"D:\\...\\Total War WARHAMMER III")
    for path, data in c.iter_files(".rigid_model_v2"):
        ...
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

# Windows extended-length prefix, built from chr() so no amount of
# escaping can get it subtly wrong: backslash backslash ? backslash.
LONG_PREFIX = chr(92) * 2 + "?" + chr(92)

# Where rpfm_cli.exe tends to be, after $RPFM_CLI and $PATH.
_RPFM_GUESSES = (
    r"C:\Users\rob\Desktop\tw modding\RPFM\rpfm_cli.exe",
    r"C:\Program Files\rpfm\rpfm_cli.exe",
)
_RPFM_SRC_GUESSES = (
    r"C:\Users\rob\CascadeProjects\rpfm",
)

# Extraction goes somewhere SHORT - see the module docstring.
DEFAULT_WORK_DIR = r"D:\twsweep" if os.path.isdir("D:\\") else \
    os.path.join(os.path.expanduser("~"), "twsweep")


class CorpusError(Exception):
    pass


def find_rpfm() -> str:
    """rpfm_cli.exe, or raise saying how to point at it."""
    from_env = os.environ.get("RPFM_CLI")
    if from_env and os.path.isfile(from_env):
        return from_env
    on_path = shutil.which("rpfm_cli") or shutil.which("rpfm_cli.exe")
    if on_path:
        return on_path
    for guess in _RPFM_GUESSES:
        if os.path.isfile(guess):
            return guess
    raise CorpusError(
        "rpfm_cli.exe not found. Set RPFM_CLI to its path, or put it on "
        "PATH. Corpus work goes through RPFM rather than a reader of our "
        "own - see this module's docstring for why.")


def find_rpfm_source():
    """RPFM's source checkout, or None. Only needed for the games that
    predate `manifest.txt` and keep their vanilla pack list in code."""
    from_env = os.environ.get("RPFM_SRC")
    if from_env and os.path.isdir(from_env):
        return from_env
    for guess in _RPFM_SRC_GUESSES:
        if os.path.isdir(guess):
            return guess
    return None


def open_binary(path: str) -> bytes:
    """Read a file, MAX_PATH or not."""
    full = os.path.abspath(path)
    if os.name == "nt" and not full.startswith(LONG_PREFIX):
        full = LONG_PREFIX + full
    with open(full, "rb") as handle:
        return handle.read()


def _vanilla_from_manifest(game_dir: str):
    manifest = os.path.join(game_dir, "data", "manifest.txt")
    if not os.path.isfile(manifest):
        return None
    names = set()
    with open(manifest, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            first = line.split("\t")[0].strip()
            if first.lower().endswith(".pack") and "/" not in first:
                names.add(first)
    return names or None


def _vanilla_from_rpfm_source(game_key: str):
    """The hardcoded list RPFM keeps for the pre-manifest games.

    Read out of `supported_games.rs` rather than copied here, so it
    cannot drift from the tool that everyone checks results against.
    """
    source_root = find_rpfm_source()
    if source_root is None:
        return None
    path = os.path.join(source_root, "rpfm_lib", "src", "games",
                        "supported_games.rs")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    # Each game is `game_list.insert(KEY_X, GameInfo { ... })`; find the
    # block for this key and take the first non-empty vanilla_packs in it.
    key_pattern = re.compile(
        r'game_list\.insert\(\s*KEY_(\w+)\s*,', re.M)
    blocks = list(key_pattern.finditer(text))
    for i, match in enumerate(blocks):
        if match.group(1).lower() != game_key.lower():
            continue
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        block = text[match.start():end]
        packs = re.findall(r'"([^"]+\.pack)"\.to_owned\(\)', block)
        cleaned = {os.path.basename(p) for p in packs}
        return cleaned or None
    return None


class Corpus:
    """One installed game, read through RPFM."""

    def __init__(self, game_key: str, game_dir: str, work_dir=None,
                 rpfm=None):
        self.game_key = game_key
        self.game_dir = game_dir
        self.data_dir = os.path.join(game_dir, "data")
        if not os.path.isdir(self.data_dir):
            raise CorpusError("no data folder under %r" % game_dir)
        self.rpfm = rpfm or find_rpfm()
        self.work_dir = work_dir or DEFAULT_WORK_DIR

    # -- pack selection ------------------------------------------------

    def vanilla_pack_names(self):
        """CA's own packs, by RPFM's rule. Raises rather than guessing."""
        names = _vanilla_from_manifest(self.game_dir)
        if names is None:
            names = _vanilla_from_rpfm_source(self.game_key)
        if names is None:
            raise CorpusError(
                "cannot tell %s's vanilla packs from mods: no "
                "data/manifest.txt, and RPFM's source (for its hardcoded "
                "list) was not found. Set RPFM_SRC to an RPFM checkout."
                % self.game_key)
        return names

    def packs(self, vanilla_only=True):
        """[absolute pack path], in load order by name."""
        wanted = self.vanilla_pack_names() if vanilla_only else None
        out = []
        for name in sorted(os.listdir(self.data_dir)):
            if not name.lower().endswith(".pack"):
                continue
            if wanted is not None and name not in wanted:
                continue
            out.append(os.path.join(self.data_dir, name))
        return out

    # -- RPFM calls ----------------------------------------------------

    def _run(self, args, timeout=3600):
        result = subprocess.run(
            [self.rpfm, "-g", self.game_key] + args,
            capture_output=True, timeout=timeout)
        return result

    def list_pack(self, pack_path: str):
        """Every file path inside one pack."""
        result = self._run(["pack", "list", "-p", pack_path])
        out = []
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            # RPFM logs to stdout as well; its lines are timestamped.
            if not line or "[INFO]" in line or "[WARN]" in line:
                continue
            out.append(line.replace("\\", "/"))
        return out

    def extract_folders(self, pack_path: str, folders):
        """Extract these top-level folders to a clean work dir."""
        shutil.rmtree(self.work_dir, ignore_errors=True)
        os.makedirs(self.work_dir, exist_ok=True)
        args = ["pack", "extract", "-p", pack_path]
        for folder in folders:
            args += ["-F", "%s/;%s" % (folder.rstrip("/"), self.work_dir)]
        self._run(args)
        return self.work_dir

    # -- the loop most sweeps want -------------------------------------

    def iter_files(self, suffix: str, vanilla_only=True, progress=True):
        """Yield (pack-relative path, bytes) for every matching file.

        Extraction is per pack and the work dir is wiped between them, so
        peak disk is one pack rather than the whole game.
        """
        packs = self.packs(vanilla_only=vanilla_only)
        for index, pack_path in enumerate(packs, 1):
            listing = [p for p in self.list_pack(pack_path)
                       if p.endswith(suffix)]
            if not listing:
                continue
            folders = sorted({p.split("/")[0] for p in listing})
            self.extract_folders(pack_path, folders)
            found = 0
            for root, _, names in os.walk(self.work_dir):
                for name in names:
                    if not name.endswith(suffix):
                        continue
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, self.work_dir)
                    found += 1
                    yield rel.replace("\\", "/"), open_binary(full)
            if progress:
                print("[%3d/%3d] %-40s listed=%-6d extracted=%-6d"
                      % (index, len(packs),
                         os.path.basename(pack_path)[:40], len(listing),
                         found), file=sys.stderr, flush=True)
            if found != len(listing):
                # Loud, because this is how a sweep goes quietly wrong.
                print("  WARNING: %s listed %d but extracted %d"
                      % (os.path.basename(pack_path), len(listing), found),
                      file=sys.stderr, flush=True)
        shutil.rmtree(self.work_dir, ignore_errors=True)


def add_addon_to_path():
    """Make the format modules importable (they are bpy-free)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    addon = os.path.join(root, "io_scene_rmv2")
    if addon not in sys.path:
        sys.path.insert(0, addon)
