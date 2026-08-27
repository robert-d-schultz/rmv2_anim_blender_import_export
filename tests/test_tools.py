"""Tests for tools/corpus.py - the corpus sweep plumbing.

Only the parts that need neither RPFM nor an installed game: the two
things that have silently corrupted a sweep before, which are worth a
regression test precisely because a wrong answer there still looks like a
healthy run.

Run with:  python tests/test_tools.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import corpus  # noqa: E402


class TestLongPaths(unittest.TestCase):
    """Extracted game trees run past Windows' 260-char MAX_PATH."""

    def test_reads_a_path_over_max_path(self):
        root = tempfile.mkdtemp()
        # Nest until well past 260 characters.
        path = root
        while len(path) < 300:
            path = os.path.join(path, "wh_variantmodels_hu1e_cth")
            try:
                os.makedirs(corpus.LONG_PREFIX + path if os.name == "nt"
                            else path, exist_ok=True)
            except OSError:
                self.skipTest("filesystem will not nest that deep")
        target = os.path.join(path, "cth_cloth_cloak_01.rigid_model_v2")
        payload = b"RMV2" + b"\0" * 64
        with open(corpus.LONG_PREFIX + target if os.name == "nt"
                  else target, "wb") as handle:
            handle.write(payload)

        self.assertGreater(len(target), 260)
        self.assertEqual(corpus.open_binary(target), payload)

    def test_prefix_is_the_real_one(self):
        # \\?\ - four characters. Getting this wrong fails every open,
        # which is at least loud; getting it *nearly* right has happened.
        self.assertEqual(corpus.LONG_PREFIX, "\\\\?\\")


class TestVanillaFilter(unittest.TestCase):
    """Mods sit beside CA's packs and use fields vanilla never does."""

    def _game_with_manifest(self, lines):
        root = tempfile.mkdtemp()
        data = os.path.join(root, "data")
        os.makedirs(data)
        with open(os.path.join(data, "manifest.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        return root

    def test_manifest_names_the_vanilla_packs(self):
        root = self._game_with_manifest([
            "data.pack\t123\t1",
            "models3.pack\t456\t1",
            "campaigns/wh3_main/startpos.esf\t99\t1",
        ])
        names = corpus._vanilla_from_manifest(root)
        self.assertEqual(names, {"data.pack", "models3.pack"})

    def test_a_mod_pack_is_not_in_the_manifest(self):
        root = self._game_with_manifest(["data.pack\t1\t1"])
        names = corpus._vanilla_from_manifest(root)
        self.assertNotIn("!!!patched_straight_walls.pack", names)

    def test_no_manifest_is_not_silently_everything(self):
        """The dangerous failure is treating 'cannot tell' as 'all of
        them'. Without a manifest and without RPFM's source, Corpus must
        refuse rather than sweep mods as if they were CA's."""
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "data"))
        self.assertIsNone(corpus._vanilla_from_manifest(root))


class TestRpfmDiscovery(unittest.TestCase):

    def test_missing_rpfm_says_how_to_fix_it(self):
        saved = os.environ.pop("RPFM_CLI", None)
        guesses = corpus._RPFM_GUESSES
        corpus._RPFM_GUESSES = ()
        try:
            import shutil
            real_which = shutil.which
            shutil.which = lambda *a, **k: None
            try:
                with self.assertRaises(corpus.CorpusError) as caught:
                    corpus.find_rpfm()
                self.assertIn("RPFM_CLI", str(caught.exception))
            finally:
                shutil.which = real_which
        finally:
            corpus._RPFM_GUESSES = guesses
            if saved is not None:
                os.environ["RPFM_CLI"] = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
