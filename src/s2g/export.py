"""Atomic TIFF/manifest export without overwriting an existing file."""

from pathlib import Path
import json
import os
import tempfile

import numpy as np
import rawpy
import tifffile

from . import __version__
from .color import linear_srgb_profile
from .raw import ProcessingError, decode, decode_options, sha256, validate_source


def write_tiff(path: Path, rgb: np.ndarray) -> None:
    if rgb.dtype != np.uint16 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ProcessingError("TIFF export requires an H x W x 3 uint16 image.")
    tifffile.imwrite(
        path, rgb, photometric="rgb", planarconfig="contig",
        compression=None, byteorder="<", metadata=None,
        software=f"S2G {__version__}",
        description="M0 neutral decode; linear sRGB; LibRaw camera matrix; no GR look",
        iccprofile=linear_srgb_profile(),
        extratags=[(274, "H", 1, 1, False)],
    )


def export_neutral(source: Path, output: Path, *, half_size: bool = False) -> dict:
    source = validate_source(source)
    output = output.resolve()
    if output.suffix.lower() not in (".tif", ".tiff"):
        raise ProcessingError("Output must end in .tif or .tiff.")
    manifest_path = output.with_suffix(output.suffix + ".json")
    for target in (output, manifest_path):
        if target.exists():
            raise ProcessingError(f"Output already exists; choose a new filename: {target}")
    rgb, metadata = decode(source, half_size=half_size)
    options = decode_options(half_size)
    options["demosaic_algorithm"] = options["demosaic_algorithm"].name
    options["output_color"] = options["output_color"].name
    options["highlight_mode"] = options["highlight_mode"].name
    manifest = {
        "schema": 1,
        "source": metadata,
        "engine": {
            "version": __version__, "rawpy": rawpy.__version__,
            "libraw": list(rawpy.libraw_version), "numpy": np.__version__,
            "profile": "libraw_builtin_m0_uncalibrated",
        },
        "decode": options,
        "output": {
            "path": str(output), "space": "linear-sRGB", "transfer": "linear",
            "bits_per_channel": 16, "width": rgb.shape[1], "height": rgb.shape[0],
            "orientation": 1,
            "clipped_fraction_per_channel": [
                float(np.count_nonzero(rgb[:, :, c] == 65535) / (rgb.shape[0] * rgb.shape[1]))
                for c in range(3)
            ],
        },
        "limitations": [
            "Built-in LibRaw camera matrix, not a fitted dual-illuminant profile.",
            "Bounded linear sRGB clips negative/out-of-gamut colors and highlights.",
            "No lens distortion/shading correction, highlight recovery or GR look yet.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    published = []
    # Temp files stay on the destination volume; hard links publish atomically
    # and fail if a concurrent process has already created the destination.
    try:
        with tempfile.TemporaryDirectory(prefix=".s2g-", dir=output.parent) as temp:
            temp_tiff = Path(temp) / "image.tif"
            temp_json = Path(temp) / "manifest.json"
            write_tiff(temp_tiff, rgb)
            manifest["output"]["sha256"] = sha256(temp_tiff)
            manifest["source_unchanged"] = sha256(source) == metadata["sha256"]
            if not manifest["source_unchanged"]:
                raise ProcessingError("Source changed before export; output cancelled.")
            temp_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            for staged, target in ((temp_tiff, output), (temp_json, manifest_path)):
                os.link(staged, target)
                published.append(target)
    except BaseException:
        for target in published:
            target.unlink(missing_ok=True)
        raise
    return manifest
