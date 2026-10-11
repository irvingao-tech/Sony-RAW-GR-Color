"""Opt-in acceptance tests against a locally owned A7C II ARW."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
import tifffile

from s2g.cli import main
from s2g.export import export_neutral
from s2g.raw import sha256
from s2g.raw import decode
from s2g.develop import export_developed
from s2g.edits import Edits
from s2g.pipeline import render_pixels, resize_camera


@unittest.skipUnless(os.environ.get("S2G_TEST_ARW"), "Set S2G_TEST_ARW to a lawful A7C II sample")
class RealSampleTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(os.environ["S2G_TEST_ARW"]).resolve()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_inspect_reports_actual_exposure_and_lens(self):
        with redirect_stdout(StringIO()) as stdout:
            status = main(["inspect", str(self.source)])
        self.assertEqual(status, 0)
        metadata = json.loads(stdout.getvalue())
        self.assertEqual(metadata["model"], "ILCE-7CM2")
        self.assertGreater(metadata["iso_speed"], 0)
        self.assertGreater(metadata["shutter_seconds"], 0)
        self.assertGreater(metadata["aperture_f_number"], 0)
        self.assertGreater(metadata["focal_length_mm"], 0)
        self.assertIn("model", metadata["lens"])

    def test_full_export_is_16bit_repeatable_and_read_only(self):
        before = sha256(self.source)
        first = self.root / "first.tif"
        second = self.root / "second.tif"
        manifest = export_neutral(self.source, first)
        export_neutral(self.source, second)
        self.assertEqual(before, sha256(self.source))
        self.assertEqual(sha256(first), sha256(second))
        with tifffile.TiffFile(first) as tif:
            page = tif.pages[0]
            self.assertEqual(page.dtype, np.dtype("uint16"))
            self.assertEqual(page.samplesperpixel, 3)
            self.assertEqual(page.bitspersample, 16)
            self.assertEqual(page.shape, (manifest["output"]["height"], manifest["output"]["width"], 3))
            self.assertIn(34675, page.tags)
            self.assertEqual(page.tags[274].value, 1)
        self.assertTrue(manifest["source_unchanged"])

    def test_half_size_decode_has_expected_resolution(self):
        manifest = export_neutral(self.source, self.root / "half.tif", half_size=True)
        width, height = manifest["source"]["visible_size"]
        if manifest["source"]["orientation_libraw"] in (5, 6):
            width, height = height, width
        self.assertEqual(manifest["output"]["width"], (width + 1) // 2)
        self.assertEqual(manifest["output"]["height"], (height + 1) // 2)
        self.assertTrue(manifest["decode"]["half_size"])

    def test_edited_tiff_and_jpeg_repeat_with_real_raw(self):
        before=sha256(self.source)
        edit=Edits(look="gr_negative_experimental_v1",grain=20,exposure_ev=.15)
        for extension in ("jpg","tif"):
            first,second=self.root/f"edited-a.{extension}",self.root/f"edited-b.{extension}"
            report=export_developed(self.source,first,edit,max_size=1600)
            export_developed(self.source,second,edit,max_size=1600)
            self.assertEqual(sha256(first),sha256(second))
            self.assertEqual(report["output"]["width"],1600)
            self.assertTrue(report["source_unchanged"])
        self.assertEqual(sha256(self.source),before)

    def test_proxy_and_full_decode_use_consistent_display_graph(self):
        full,meta=decode(self.source,sensor=True)
        proxy=resize_camera(full,1400)
        for look in ("gr_positive_experimental_v1", "gr_positive_experimental_v2", "gr_negative_experimental_v2"):
            edit=Edits(look=look)
            expected=render_pixels(full,meta,edit,max_size=1400)
            preview=render_pixels(proxy,meta,edit,max_size=1400)
            np.testing.assert_array_equal(expected,preview)

    def test_v2_real_exports_are_repeatable_and_share_precision(self):
        edit=Edits(look="gr_negative_experimental_v2", grain=20, exposure_ev=.15,
                   saturation=20, vibrance=35, split_toning=140)
        first,second=self.root/"v2-a.tif",self.root/"v2-b.tif"
        report=export_developed(self.source,first,edit,max_size=1600)
        export_developed(self.source,second,edit,max_size=1600)
        self.assertEqual(sha256(first),sha256(second))
        self.assertEqual(report["engine"]["graph"],"float32_ap1_oklab_sdr_v2+color_controls_v1")
        camera,metadata=decode(self.source,sensor=True)
        expected=render_pixels(camera,metadata,edit,max_size=1600,bits=8)
        actual=tifffile.imread(first)
        np.testing.assert_allclose(expected.astype(float)/255,actual.astype(float)/65535,atol=.5/255+.5/65535)
