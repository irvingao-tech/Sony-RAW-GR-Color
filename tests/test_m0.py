from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageCms
import tifffile

from s2g.cli import main
from s2g.color import linear_srgb_profile
from s2g.export import export_neutral, write_tiff
from s2g.raw import ProcessingError, camera_identity, sha256, validate_source


class M0Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "测试.ARW"
        self.source.write_bytes(b"simulated RAW source for export tests")
        self.rgb = np.arange(36, dtype=np.uint16).reshape(3, 4, 3) * 1700
        self.metadata = {"sha256": sha256(self.source), "model": "ILCE-7CM2"}

    def render_mock(self, output):
        with patch("s2g.export.decode", return_value=(self.rgb, self.metadata)):
            return export_neutral(self.source, output)

    def test_linear_icc_has_correct_transfer_and_primaries(self):
        profile = ImageCms.ImageCmsProfile(BytesIO(linear_srgb_profile()))
        self.assertIn("Linear sRGB", ImageCms.getProfileDescription(profile))
        image = Image.new("RGB", (1, 1), (64, 128, 192))
        converted = ImageCms.profileToProfile(image, profile, ImageCms.createProfile("sRGB"))
        np.testing.assert_allclose(converted.getpixel((0, 0)), (137, 188, 225), atol=1)

    def test_tiff_preserves_uint16_rgb_and_icc(self):
        output = self.root / "linear.tif"
        write_tiff(output, self.rgb)
        with tifffile.TiffFile(output) as tif:
            page = tif.pages[0]
            self.assertEqual(page.bitspersample, 16)
            self.assertEqual(page.tags[274].value, 1)
            self.assertEqual(page.tags[34675].value, linear_srgb_profile())
            np.testing.assert_array_equal(page.asarray(), self.rgb)

    def test_tiff_is_byte_deterministic(self):
        first, second = self.root / "a.tif", self.root / "b.tif"
        write_tiff(first, self.rgb)
        write_tiff(second, self.rgb)
        self.assertEqual(sha256(first), sha256(second))

    def test_tiff_rejects_eight_bit_input(self):
        with self.assertRaises(ProcessingError):
            write_tiff(self.root / "bad.tif", self.rgb.astype(np.uint8))

    def test_export_manifest_hashes_and_source_preservation(self):
        before = self.source.read_bytes()
        output = self.root / "导出" / "neutral.tiff"
        result = self.render_mock(output)
        saved = json.loads(output.with_suffix(".tiff.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, json.loads(json.dumps(result)))
        self.assertTrue(saved["source_unchanged"])
        self.assertEqual(saved["output"]["sha256"], sha256(output))
        self.assertEqual(self.source.read_bytes(), before)

    def test_existing_tiff_is_preserved(self):
        output = self.root / "existing.tif"
        output.write_bytes(b"existing output")
        with self.assertRaises(ProcessingError):
            self.render_mock(output)
        self.assertEqual(output.read_bytes(), b"existing output")

    def test_existing_manifest_is_preserved(self):
        output = self.root / "existing.tif"
        manifest = output.with_suffix(".tif.json")
        manifest.write_bytes(b"existing manifest")
        with self.assertRaises(ProcessingError):
            self.render_mock(output)
        self.assertFalse(output.exists())
        self.assertEqual(manifest.read_bytes(), b"existing manifest")

    def test_failed_manifest_publish_rolls_back_tiff(self):
        output = self.root / "failed.tif"
        original_link = os.link
        def link(staged, target):
            if str(target).endswith(".json"):
                raise OSError("simulated disk/publish failure")
            return original_link(staged, target)
        with patch("s2g.export.os.link", side_effect=link), self.assertRaises(OSError):
            self.render_mock(output)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(".tif.json").exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [self.source.name])

    def test_source_change_cancels_export(self):
        self.metadata["sha256"] = "wrong hash"
        output = self.root / "changed.tif"
        with self.assertRaises(ProcessingError):
            self.render_mock(output)
        self.assertFalse(output.exists())

    def test_non_arw_rejected(self):
        with self.assertRaises(ProcessingError):
            validate_source(self.root / "image.jpg")

    def test_missing_source_rejected(self):
        with self.assertRaises(ProcessingError):
            validate_source(self.root / "missing.arw")

    def test_camera_restriction_uses_metadata(self):
        for model in ("ILCE-7CM2", "ILCE-7M4"):
            source = self.root / f"{model}.ARW"
            tifffile.imwrite(source, np.zeros((2, 2), dtype=np.uint16), extratags=[
                (271, "s", 0, "SONY", False), (272, "s", 0, model, False),
            ])
            if model == "ILCE-7CM2":
                self.assertEqual(camera_identity(source)["model"], model)
            else:
                with self.assertRaises(ProcessingError):
                    camera_identity(source)

    def test_corrupt_arw_has_readable_cli_error(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
            code = main(["inspect", str(self.source)])
        self.assertEqual(code, 1)
        self.assertIn("Cannot read ARW camera metadata", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_invalid_export_extension(self):
        with self.assertRaises(ProcessingError):
            self.render_mock(self.root / "image.jpg")


if __name__ == "__main__":
    unittest.main()
