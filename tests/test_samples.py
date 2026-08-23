"""Round-trip every file in samples/ - the real ones, not fixtures.

The unit tests in test_format.py build their inputs, which means they can
only ever check the reader against the writer's idea of the format.
These files came out of the games untouched, so they check both against
CA.

Run with:  python tests/test_samples.py       (or under pytest)

Skips itself when samples/ is not present, since the folder holds
Creative Assembly's assets and may not travel with the repo.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "io_scene_rmv2"))
import anim_format  # noqa: E402
import arm_format  # noqa: E402
import rmv2_format  # noqa: E402
import vmpf_format  # noqa: E402
import vwm_format  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "samples")

LOADERS = {
    ".anim": (anim_format.load, anim_format.save),
    ".rigid_model": (arm_format.load, arm_format.save),
    ".animatable_rigid_model": (arm_format.load, arm_format.save),
    # The object list runs straight into a whole headerless .anim.
    ".rigid_model_animation": (
        lambda data: arm_format.load(data, allow_trailing=True),
        arm_format.save),
    ".rigid_model_v2": (rmv2_format.load, rmv2_format.save),
    ".variant_part_mesh": (vmpf_format.load, vmpf_format.save),
    ".variant_weighted_mesh": (vwm_format.load, vwm_format.save),
}


def find_samples():
    if not os.path.isdir(SAMPLES):
        return []
    out = []
    for game in sorted(os.listdir(SAMPLES)):
        folder = os.path.join(SAMPLES, game)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() in LOADERS:
                out.append((game, name, os.path.join(folder, name)))
    return out


class TestSamples(unittest.TestCase):
    """Every sample must re-save as the bytes it was read from.

    The one licensed exception is .rigid_model_v2, where the writer
    deliberately stamps rmv2_format.EXPORT_SIGNATURE over the three
    padding bytes in each LOD header so exported files are identifiable.
    Nothing else may move.
    """

    def test_round_trip(self):
        samples = find_samples()
        if not samples:
            self.skipTest("no samples/ folder in this checkout")
        for game, name, path in samples:
            with self.subTest(game=game, file=name):
                with open(path, "rb") as handle:
                    blob = handle.read()
                load, save = LOADERS[os.path.splitext(name)[1].lower()]
                model = load(blob)
                out = save(model)
                self.assertEqual(
                    len(out), len(blob),
                    f"{game}/{name}: re-saved to a different length")
                if out == blob:
                    continue

                differing = [i for i in range(len(blob))
                             if blob[i] != out[i]]
                self.assertEqual(
                    os.path.splitext(name)[1].lower(), ".rigid_model_v2",
                    f"{game}/{name}: {len(differing)} byte(s) changed at "
                    f"{differing[:8]}, and only .rigid_model_v2 is allowed "
                    "to differ at all")
                signature = set(rmv2_format.EXPORT_SIGNATURE)
                self.assertTrue(
                    all(out[i] in signature for i in differing),
                    f"{game}/{name}: changed bytes outside the export "
                    f"signature at {differing[:8]}")
                self.assertLessEqual(
                    len(differing), 3 * len(model.lods),
                    f"{game}/{name}: more changed bytes than the "
                    f"{len(model.lods)} LOD signature(s) can account for")

    def test_writer_is_stable(self):
        """Whatever the writer does to a file, doing it twice changes
        nothing more - so the signature above is the end of it."""
        samples = find_samples()
        if not samples:
            self.skipTest("no samples/ folder in this checkout")
        for game, name, path in samples:
            with self.subTest(game=game, file=name):
                with open(path, "rb") as handle:
                    blob = handle.read()
                load, save = LOADERS[os.path.splitext(name)[1].lower()]
                once = save(load(blob))
                self.assertEqual(save(load(once)), once,
                                 f"{game}/{name}: a second pass moved")


if __name__ == "__main__":
    found = find_samples()
    print("%d sample file(s) under %s"
          % (len(found), os.path.normpath(SAMPLES)))
    unittest.main(verbosity=2)
