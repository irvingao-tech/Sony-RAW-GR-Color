"""Develop edited images through the shared scene graph and publish safely."""
from pathlib import Path
import json
import os
import tempfile

import numpy as np
from PIL import Image, __version__ as pillow_version
import rawpy
import tifffile

from . import __version__
from .color import srgb_profile
from .edits import Edits, PROFILE_ID, load_sidecar, sidecar_document
from .pipeline import engine_identity, render_pixels
from .raw import ProcessingError, decode, sha256, validate_source


def export_developed(source: Path, output: Path, edit: Edits | None = None, *, max_size=None, progress=None, cancelled=None):
    source = validate_source(source)
    output = output.resolve()
    if output.suffix.lower() not in (".jpg", ".jpeg", ".tif", ".tiff"):
        raise ProcessingError("Output must be JPEG or TIFF.")
    if max_size is not None and (type(max_size) is not int or not 64 <= max_size <= 16000):
        raise ProcessingError("Export long edge must be between 64 and 16000 pixels.")
    manifest_path = output.with_suffix(output.suffix + ".json")
    for target in (output, manifest_path):
        if target.exists():
            raise ProcessingError(f"Output already exists; choose a new filename: {target}")
    edit = load_sidecar(source) if edit is None else Edits.from_dict(edit.to_dict())
    camera, metadata = decode(source, sensor=True)
    if progress:
        progress(.1)
    is_tiff = output.suffix.lower() in (".tif", ".tiff")
    rgb = render_pixels(camera, metadata, edit, bits=16 if is_tiff else 8, max_size=max_size,
                        progress=(lambda p: progress(.1 + p * .8)) if progress else None, cancelled=cancelled)
    del camera
    manifest = {
        "schema": 2, "source": metadata,
        "engine": {**engine_identity(edit), "application_version": __version__,
                   "graph": ("float32_ap1_oklab_sdr_v2" if edit.look.endswith("_v2") else "float32_ap1_sdr_v1") + ("+color_controls_v1" if edit.color_controls_active else ""),
                   "rawpy": rawpy.__version__, "libraw": list(rawpy.libraw_version),
                   "numpy": np.__version__, "pillow": pillow_version, "tifffile": tifffile.__version__},
        "sidecar": sidecar_document(source, edit, metadata["sha256"]),
        "output": {"path": str(output), "space": "sRGB", "transfer": "sRGB",
                   "width": rgb.shape[1], "height": rgb.shape[0], "bits_per_channel": 16 if is_tiff else 8,
                   "quality": None if is_tiff else 95, "max_size": max_size},
        "calibrated": False,
        "limitations": ["Experimental GR-inspired parameters; no Ricoh reference fitting yet.",
                        "Uncalibrated single-illuminant built-in camera profile.",
                        "Custom SDR display transform; not an official ACES output transform.",
                        "No lens correction or reconstruction of sensor-clipped highlights."],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    published = []
    try:
        with tempfile.TemporaryDirectory(prefix=".s2g-", dir=output.parent) as temp:
            image_path, json_path = Path(temp) / "image", Path(temp) / "manifest.json"
            if is_tiff:
                tifffile.imwrite(image_path, rgb, photometric="rgb", planarconfig="contig", byteorder="<",
                                 compression=None, metadata=None, iccprofile=srgb_profile(),
                                 software=f"S2G {__version__}", description="S2G edited sRGB output",
                                 extratags=[(274, "H", 1, 1, False)])
            else:
                Image.fromarray(rgb).save(image_path, format="JPEG", quality=95, subsampling=0,
                                          optimize=False, progressive=False, icc_profile=srgb_profile())
            manifest["output"]["sha256"] = sha256(image_path)
            manifest["source_unchanged"] = sha256(source) == metadata["sha256"]
            if not manifest["source_unchanged"]:
                raise ProcessingError("Source changed before publishing; export cancelled.")
            if cancelled and cancelled():
                raise ProcessingError("Export cancelled.")
            json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            for staged, target in ((image_path, output), (json_path, manifest_path)):
                os.link(staged, target)
                published.append(target)
    except BaseException:
        for target in published:
            target.unlink(missing_ok=True)
        raise
    if progress:
        progress(1)
    return manifest
