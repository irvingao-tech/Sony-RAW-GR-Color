from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from s2g.appearance import (from_oklab, neutral_luma_matrix, perceptual_color,
                            to_oklab, tone_map, tone_table)
from s2g.edits import Edits, load_sidecar, save_sidecar, sidecar_path
from s2g.pipeline import AP1_LUMA, LOOKS, display_graph, engine_identity
from s2g.raw import ProcessingError

V2 = ("gr_positive_experimental_v2", "gr_negative_experimental_v2")


class AppearanceTests(unittest.TestCase):
    def test_oklab_roundtrip_signed_values(self):
        rgb = np.random.default_rng(5).uniform(-.1, 3, (50, 30, 3)).astype(np.float32)
        # Float32 cube-root/matrix roundoff stays below one 16-bit code value.
        np.testing.assert_allclose(from_oklab(to_oklab(rgb)), rgb, atol=8e-6)

    def test_style_matrix_preserves_luminance_and_gray(self):
        for name in V2:
            matrix = neutral_luma_matrix(LOOKS[name])
            np.testing.assert_allclose(matrix @ np.ones(3), np.ones(3), atol=3e-7)
            np.testing.assert_allclose(AP1_LUMA @ matrix, AP1_LUMA, atol=3e-7)

    def test_smooth_tone_is_monotone_with_finite_headroom(self):
        for name in V2:
            look = LOOKS[name]
            x, y = tone_table(tuple(look["tone_x"]), tuple(look["tone_y"]))
            self.assertEqual(len(x), 1024)
            self.assertGreater(np.diff(y).min(), 0)
            values = tone_map(np.linspace(-32, 32, 10000), look)
            self.assertTrue(np.isfinite(values).all())
            self.assertGreater(np.diff(values).min(), 0)
            np.testing.assert_allclose(np.diff(values[:100]), 64/9999, atol=4e-6)

    def test_all_strengths_gray_ramp_monotonic_and_neutral(self):
        ramp = np.repeat(np.r_[0, np.geomspace(1e-8, 256, 3000)][:, None], 3, axis=1).astype(np.float32)
        for name in V2:
            for strength in (0, .25, .82, 1):
                result = display_graph(ramp, Edits(look=name, strength=strength))
                self.assertTrue(np.isfinite(result).all())
                self.assertGreaterEqual(np.diff(result[:, 0]).min(), -2e-6)
                np.testing.assert_allclose(result[:, 0], result[:, 2], atol=2e-5)
                self.assertTrue(((result >= 0) & (result <= 1)).all())

    def test_zero_strength_exactly_matches_neutral_with_user_adjustments(self):
        scene = np.random.default_rng(5).uniform(-.1, 3, (10, 20, 3)).astype(np.float32)
        edit = Edits(exposure_ev=.3, shadows=25, highlights=35)
        neutral = display_graph(scene, edit)
        for name in V2:
            np.testing.assert_array_equal(neutral, display_graph(scene, replace(edit, look=name, strength=0)))

    def test_negative_black_floor_is_perceptual_and_restrained(self):
        black = np.zeros((1, 3), np.float32)
        old = display_graph(black, Edits(look="gr_negative_experimental_v1", strength=1))
        new = display_graph(black, Edits(look=V2[1], strength=1))
        self.assertGreater(float(old.min()), .15)
        np.testing.assert_allclose(new, .026, atol=1e-6)

    def test_skin_protection_reduces_warm_chroma_changes(self):
        lab = np.array([[.65, .09*np.cos(np.radians(55)), .09*np.sin(np.radians(55))]], np.float32)
        rgb = from_oklab(lab)
        for name in V2:
            look = LOOKS[name]
            protected = to_oklab(perceptual_color(rgb, look, 1))
            unprotected = to_oklab(perceptual_color(rgb, dict(look, skin_protection=0), 1))
            self.assertLess(np.linalg.norm(protected-lab), np.linalg.norm(unprotected-lab)*.5)

    def test_legacy_sidecar_keeps_engine_identity_and_v2_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"test.ARW"
            source.write_bytes(b"unchanged raw")
            for name in ("gr_positive_experimental_v1", *V2):
                edit = Edits(look=name)
                with patch("s2g.raw.camera_identity"):
                    save_sidecar(source, edit)
                self.assertEqual(load_sidecar(source), edit)
                identity = engine_identity(edit)
                self.assertEqual(identity["version"], "0.2.0" if name.endswith("v1") else "0.3.0")
                document = json.loads(sidecar_path(source).read_text())
                document["edits"]["look"] = V2[1] if name.endswith("v1") else "gr_positive_experimental_v1"
                sidecar_path(source).write_text(json.dumps(document))
                with self.assertRaises(ProcessingError):
                    load_sidecar(source)
