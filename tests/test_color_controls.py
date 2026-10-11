from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import tifffile

from s2g.appearance import from_oklab, to_oklab
from s2g.color_controls import adjust_color, decode_srgb, encode_srgb, fit_oklab_gamut
from s2g.develop import export_developed
from s2g.edits import Edits, load_sidecar, save_sidecar, sidecar_path
from s2g.pipeline import CAM_XYZ, display_graph, engine_identity, render_pixels
from s2g.raw import ProcessingError, sha256


class ColorControlTests(unittest.TestCase):
    def test_inactive_controls_preserve_pixels_exactly(self):
        encoded=np.random.default_rng(5).uniform(0,1,(15,20,3)).astype(np.float32)
        self.assertIs(adjust_color(encoded,Edits()),encoded)
        values=Edits().to_dict()
        for field in ("saturation","vibrance","split_toning"):
            self.assertNotIn(field,values)
        for look in ("neutral_v1","gr_positive_experimental_v1","gr_negative_experimental_v2"):
            self.assertNotEqual(engine_identity(Edits(look=look))["version"],"0.4.0")

    def test_monochrome_is_achromatic_with_lightness_preserved(self):
        encoded=np.array([[.3,.6,.2],[.85,.4,.1],[.1,.4,.8]],np.float32)
        before=to_oklab(decode_srgb(encoded))
        output=adjust_color(encoded,Edits(saturation=-100,vibrance=100))
        np.testing.assert_allclose(output[:,0],output[:,2],atol=2e-6)
        np.testing.assert_allclose(to_oklab(decode_srgb(output))[:,0],before[:,0],atol=2e-6)

    def test_vibrance_favors_muted_colors_and_reduces_skin_changes(self):
        hue=np.radians([260,260,55])
        chroma=np.array([.03,.13,.09])
        lab=np.column_stack((np.full(3,.6),chroma*np.cos(hue),chroma*np.sin(hue))).astype(np.float32)
        encoded=encode_srgb(from_oklab(lab))
        result=to_oklab(decode_srgb(adjust_color(encoded,Edits(vibrance=70))))
        ratio=np.hypot(result[:,1],result[:,2])/chroma
        self.assertGreater(ratio[0],ratio[1])
        self.assertGreater(ratio[1],ratio[2])
        self.assertGreater(ratio[2],1)

    def test_gamut_reduction_preserves_oklab_lightness_and_hue(self):
        hue=np.linspace(0,2*np.pi,90,endpoint=False)
        lab=np.column_stack((np.full(90,.6),.45*np.cos(hue),.45*np.sin(hue))).astype(np.float32)
        result=fit_oklab_gamut(lab)
        self.assertTrue(((result>=0)&(result<=1)).all())
        actual=to_oklab(result)
        np.testing.assert_allclose(actual[:,0],lab[:,0],atol=3e-6)
        angle=np.arctan2(actual[:,2],actual[:,1])
        error=(angle-hue+np.pi)%(2*np.pi)-np.pi
        self.assertLess(np.abs(error).max(),.001)
        self.assertTrue((np.hypot(actual[:,1],actual[:,2])<.45).all())

    def test_extreme_controls_keep_gray_ramps_and_output_finite(self):
        ramp=np.repeat(np.linspace(0,1,512)[:,None],3,axis=1).astype(np.float32)
        colored=np.random.default_rng(7).uniform(0,1,(50,3)).astype(np.float32)
        for saturation,vibrance in ((100,100),(-100,-100),(0,100),(100,-100)):
            edit=Edits(saturation=saturation,vibrance=vibrance)
            output=adjust_color(ramp,edit)
            np.testing.assert_allclose(output[:,0],output[:,2],atol=3e-6)
            self.assertGreaterEqual(np.diff(output[:,0]).min(),-2e-6)
            output=adjust_color(colored,edit)
            self.assertTrue(np.isfinite(output).all())
            self.assertTrue(((output>=0)&(output<=1)).all())

    def test_split_tone_only_changes_color_and_zero_style_remains_neutral(self):
        scene=np.array([[[.09,.12,.2],[.6,.4,.15]]],np.float32)
        for look in ("gr_positive_experimental_v1","gr_negative_experimental_v2"):
            edit=Edits(look=look)
            normal=display_graph(scene,edit)
            off=display_graph(scene,replace(edit,split_toning=0))
            self.assertGreater(np.abs(normal-off).max(),1e-5)
            # A neutral ramp remains unaffected by the protected tint operation.
            ramp=np.full((1,3),.18,np.float32)
            np.testing.assert_allclose(display_graph(ramp,edit),display_graph(ramp,replace(edit,split_toning=200)),atol=2e-6)
        edit=Edits(look="gr_positive_experimental_v2",strength=0,saturation=20,vibrance=35,split_toning=200)
        np.testing.assert_array_equal(display_graph(scene,edit),display_graph(scene,replace(edit,look="neutral_v1")))

    def test_invalid_controls_rejected(self):
        for value in ({"saturation":101},{"vibrance":float("nan")},{"split_toning":-1},{"vibrance":True}):
            with self.subTest(value=value),self.assertRaises(ProcessingError):
                Edits.from_dict(value)

    def test_sidecar_versions_and_control_hash_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"source.ARW"
            source.write_bytes(b"unchanged source")
            edit=Edits(look="gr_positive_experimental_v2",saturation=20,vibrance=35,split_toning=140)
            with patch("s2g.raw.camera_identity"):
                document=save_sidecar(source,edit)
            self.assertEqual(load_sidecar(source),edit)
            self.assertEqual(document["engine"]["version"],"0.4.0")
            self.assertEqual(document["engine"]["base_version"],"0.3.0")
            document["engine"]["color_controls_sha256"]="changed"
            sidecar_path(source).write_text(json.dumps(document))
            with self.assertRaises(ProcessingError):load_sidecar(source)

    def test_tiled_rendering_is_independent_of_pixel_batch_size(self):
        scene=np.random.default_rng(5).uniform(0,2,(260,40,3)).astype(np.float32)
        edit=Edits(look="gr_positive_experimental_v2",saturation=100,vibrance=100,split_toning=200)
        full=display_graph(scene,edit)
        tiled=np.concatenate([display_graph(scene[i:i+37],edit) for i in range(0,260,37)])
        np.testing.assert_array_equal(full,tiled)

    def test_color_controls_export_deterministically_in_both_precisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/"source.ARW"
            source.write_bytes(b"unchanged raw")
            metadata={"sha256":sha256(source),"camera_rgb_to_xyz_matrix":CAM_XYZ.tolist(),
                      "black_level_per_channel":[512]*4,"white_level":16383,"as_shot_white_balance":[2,1,1.5,1]}
            camera=np.random.default_rng(2).integers(0,14000,(130,50,3),dtype=np.uint16)
            edit=Edits(look="gr_positive_experimental_v2",saturation=40,vibrance=60,split_toning=160,grain=10)
            with patch("s2g.develop.decode",return_value=(camera,metadata)):
                for extension in ("jpg","tif"):
                    first,second=root/f"a.{extension}",root/f"b.{extension}"
                    report=export_developed(source,first,edit)
                    export_developed(source,second,edit)
                    self.assertEqual(sha256(first),sha256(second))
                    self.assertTrue(report["engine"]["graph"].endswith("+color_controls_v1"))
                actual=tifffile.imread(root/"a.tif")
                expected=render_pixels(camera,metadata,edit,bits=8)
                np.testing.assert_allclose(actual.astype(float)/65535,expected.astype(float)/255,atol=.5/255+.5/65535)
