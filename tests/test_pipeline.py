from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageCms
import tifffile

from s2g.color import srgb_profile
from s2g.develop import export_developed
from s2g.edits import Edits, load_sidecar, save_sidecar, sidecar_path
from s2g.pipeline import (ACES_WHITE, AP1_TO_SRGB, CAMERA_TO_AP1, CAM_XYZ, D65,
                          LOOKS, adaptation, display_graph, geometry, render_pixels, scene_transform, xyz_white)
from s2g.raw import ProcessingError, sha256


def metadata(source):
    return {"sha256": sha256(source), "camera_rgb_to_xyz_matrix": CAM_XYZ.tolist(),
            "black_level_per_channel": [512]*4, "white_level": 16383,
            "as_shot_white_balance": [2,1,1.5,1]}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "image.ARW"
        self.source.write_bytes(b"test source bytes")
        self.metadata = metadata(self.source)
        self.camera = np.arange(180, dtype=np.uint16).reshape(6,10,3) * 60

    def test_bradford_maps_d65_to_aces_white(self):
        np.testing.assert_allclose(adaptation(D65, ACES_WHITE) @ xyz_white(D65), xyz_white(ACES_WHITE), atol=1e-10)

    def test_neutral_axis_round_trip_is_identity(self):
        np.testing.assert_allclose(CAMERA_TO_AP1 @ np.ones(3), np.ones(3), atol=1e-6)
        np.testing.assert_allclose(AP1_TO_SRGB @ np.ones(3), np.ones(3), atol=1e-6)

    def test_signed_values_and_headroom_survive_scene_conversion(self):
        camera = np.array([[[0,0,15871],[15871,15871,15871]]],dtype=np.uint16)
        scene = scene_transform(camera,self.metadata,Edits())
        self.assertEqual(scene.dtype,np.float32)
        self.assertLess(scene.min(),0)
        self.assertGreater(scene.max(),1)
        self.assertTrue(np.isfinite(scene).all())

    def test_neutral_display_ramp_remains_monotonic_and_achromatic(self):
        ramp=np.repeat(np.geomspace(.00001,32,1000)[:,None],3,axis=1)[None].astype(np.float32)
        display=display_graph(ramp,Edits())
        self.assertGreaterEqual(np.diff(display[0,:,0]).min(),-1e-6)
        np.testing.assert_allclose(display[...,0],display[...,1],atol=1e-5)
        self.assertTrue((display>=0).all() and (display<=1).all())

    def test_all_look_tone_curves_are_monotonic(self):
        for look in LOOKS.values():
            self.assertTrue((np.diff(look["tone_x"])>0).all())
            self.assertTrue((np.diff(look["tone_y"])>0).all())

    def test_zero_strength_matches_neutral(self):
        neutral=render_pixels(self.camera,self.metadata,Edits())
        for look in ("gr_positive_experimental_v1","gr_negative_experimental_v1"):
            np.testing.assert_array_equal(neutral,render_pixels(self.camera,self.metadata,Edits(look=look,strength=0)))

    def test_exposure_brightens_middle_gray(self):
        a=display_graph(np.full((1,1,3),.18,dtype=np.float32),Edits())
        b=display_graph(np.full((1,1,3),.18,dtype=np.float32),Edits(exposure_ev=1))
        self.assertTrue((b>a).all())

    def test_seeded_grain_is_repeatable_and_changes_with_seed(self):
        edit=Edits(grain=50)
        a=render_pixels(self.camera,self.metadata,edit)
        np.testing.assert_array_equal(a,render_pixels(self.camera,self.metadata,edit))
        self.assertFalse(np.array_equal(a,render_pixels(self.camera,self.metadata,replace(edit,seed=43))))

    def test_crop_then_clockwise_rotation(self):
        edit=Edits(crop=(.2,0,.5,1),rotation=90)
        expected=np.rot90(self.camera[:,2:7],k=-1)
        np.testing.assert_array_equal(geometry(self.camera,edit),expected)

    def test_eight_and_sixteen_bit_paths_share_math(self):
        a=render_pixels(self.camera,self.metadata,Edits(),bits=8).astype(float)/255
        b=render_pixels(self.camera,self.metadata,Edits(),bits=16).astype(float)/65535
        np.testing.assert_allclose(a,b,atol=1/255)

    def test_parameter_validation_rejects_invalid_and_unknown_data(self):
        for data in ({"exposure_ev":float("nan")},{"strength":2},{"temperature":0},{"crop":[0,0,2,1]},
                     {"rotation":45},{"seed":True},{"mystery":1},{"look":"unknown"}):
            with self.subTest(data=data),self.assertRaises(ProcessingError):Edits.from_dict(data)

    def test_sidecar_round_trip_is_read_only(self):
        original=sha256(self.source)
        edit=Edits(look="gr_negative_experimental_v1",exposure_ev=.3,crop=[.1,.1,.8,.8])
        with patch("s2g.raw.camera_identity"):
            save_sidecar(self.source,edit)
        self.assertEqual(load_sidecar(self.source),edit)
        self.assertEqual(sha256(self.source),original)

    def test_sidecar_rejects_wrong_source_and_profile(self):
        with patch("s2g.raw.camera_identity"):save_sidecar(self.source,Edits())
        doc=json.loads(sidecar_path(self.source).read_text())
        doc["engine"]["profile_sha256"]="wrong"
        sidecar_path(self.source).write_text(json.dumps(doc))
        with self.assertRaises(ProcessingError):load_sidecar(self.source)
        with patch("s2g.raw.camera_identity"):save_sidecar(self.source,Edits())
        self.source.write_bytes(b"modified raw")
        with self.assertRaises(ProcessingError):load_sidecar(self.source)

    def test_jpeg_and_tiff_exports_are_tagged_and_deterministic(self):
        edit=Edits(look="gr_positive_experimental_v1",grain=30)
        with patch("s2g.develop.decode",return_value=(self.camera,self.metadata)):
            for extension in ("jpg","tif"):
                first,second=self.root/f"first.{extension}",self.root/f"second.{extension}"
                manifest=export_developed(self.source,first,edit)
                export_developed(self.source,second,edit)
                self.assertEqual(sha256(first),sha256(second))
                self.assertTrue(manifest["source_unchanged"])
                if extension=="tif":
                    with tifffile.TiffFile(first) as tif:
                        self.assertEqual(tif.pages[0].dtype,np.dtype("uint16"))
                        self.assertEqual(tif.pages[0].tags[34675].value,srgb_profile())
                else:
                    with Image.open(first) as image:self.assertEqual(image.info["icc_profile"],srgb_profile())

    def test_cancelled_export_publishes_nothing(self):
        with patch("s2g.develop.decode",return_value=(self.camera,self.metadata)),self.assertRaises(ProcessingError):
            export_developed(self.source,self.root/"cancel.jpg",Edits(),cancelled=lambda:True)
        self.assertFalse((self.root/"cancel.jpg").exists())

    def test_changed_raw_camera_matrix_is_rejected(self):
        self.metadata["camera_rgb_to_xyz_matrix"]=(np.eye(3)).tolist()
        with self.assertRaises(ProcessingError):render_pixels(self.camera,self.metadata,Edits())
