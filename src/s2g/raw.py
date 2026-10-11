"""Read-only A7C II decoder; calibrated working-space conversion comes in M1."""

from pathlib import Path
import hashlib

import numpy as np
import rawpy
import tifffile


class ProcessingError(Exception):
    """A readable input or processing failure."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_source(path: Path) -> Path:
    path = path.resolve()
    if path.suffix.lower() != ".arw":
        raise ProcessingError("Only Sony A7C II .ARW input is supported.")
    if not path.is_file():
        raise ProcessingError(f"Source file not found: {path}")
    return path


def camera_identity(path: Path) -> dict:
    # ARW is TIFF based. Read tags only, never its embedded JPEG or pixel data.
    try:
        with tifffile.TiffFile(path) as tif:
            tags = tif.pages[0].tags
            make = str(tags[271].value).strip().strip("\x00")
            model = str(tags[272].value).strip().strip("\x00")
    except (ValueError, KeyError, IndexError, OSError) as exc:
        raise ProcessingError(f"Cannot read ARW camera metadata: {exc}") from exc
    if make.upper() != "SONY" or model.upper() != "ILCE-7CM2":
        raise ProcessingError(f"Unsupported camera: {make} {model}; expected SONY ILCE-7CM2.")
    return {"make": make, "model": model}


def decode_options(half_size: bool = False) -> dict:
    return {
        "demosaic_algorithm": rawpy.DemosaicAlgorithm.AHD,
        "half_size": half_size,
        "use_camera_wb": True,
        "use_auto_wb": False,
        "output_color": rawpy.ColorSpace.sRGB,
        "output_bps": 16,
        "gamma": (1, 1),
        "no_auto_bright": True,
        "no_auto_scale": False,  # Keep black/white level normalization.
        "adjust_maximum_thr": 0.0,  # No scene-dependent white-level guessing.
        "bright": 1.0,
        "highlight_mode": rawpy.HighlightMode.Clip,
        "exp_shift": 1.0,
        "exp_preserve_highlights": 0.0,
        "median_filter_passes": 0,
    }


def decode(path: Path, *, half_size: bool = False, pixels: bool = True, sensor: bool = False):
    path = validate_source(path)
    identity = camera_identity(path)
    source_hash = sha256(path)
    try:
        with rawpy.imread(str(path)) as raw:
            wb = np.asarray(raw.camera_whitebalance, dtype=float)
            if len(wb) != 4 or not np.isfinite(wb).all() or (wb[:3] <= 0).any():
                raise ProcessingError("Missing or invalid as-shot white balance.")
            metadata = {
                **identity,
                "path": str(path),
                "sha256": source_hash,
                "size_bytes": path.stat().st_size,
                "raw_size": [raw.sizes.raw_width, raw.sizes.raw_height],
                "visible_size": [raw.sizes.width, raw.sizes.height],
                "orientation_libraw": raw.sizes.flip,
                "black_level_per_channel": list(raw.black_level_per_channel),
                "white_level": int(raw.white_level),
                "white_level_per_channel": raw.camera_white_level_per_channel,
                "as_shot_white_balance": wb.tolist(),
                "camera_rgb_to_xyz_matrix": raw.rgb_xyz_matrix.tolist(),
                "cfa_pattern": None if raw.raw_pattern is None else raw.raw_pattern.tolist(),
                "color_channels": raw.color_desc.decode("ascii", errors="replace"),
            }
            other = raw.other
            for key, name in (
                ("iso_speed", "iso_speed"), ("shutter_seconds", "shutter_speed"),
                ("aperture_f_number", "aperture"), ("focal_length_mm", "focal_length"),
            ):
                value = float(getattr(other, name))
                metadata[key] = value if np.isfinite(value) else None
            metadata["lens"] = {"make": raw.lens.make, "model": raw.lens.model}
            options = decode_options(half_size)
            if sensor:
                options.update(output_color=rawpy.ColorSpace.raw, no_auto_scale=True,
                               use_camera_wb=False, user_wb=[1, 1, 1, 1])
            rgb = raw.postprocess(**options) if pixels else None
            if sensor:
                metadata["sensor_decode"] = "libraw_black_subtracted_unscaled_camera_rgb_v1"
    except rawpy.LibRawError as exc:
        raise ProcessingError(f"LibRaw could not decode this ARW: {exc}") from exc
    if sha256(path) != source_hash:
        raise ProcessingError("Source changed during processing; output cancelled.")
    if rgb is not None and (rgb.dtype != np.uint16 or rgb.ndim != 3 or rgb.shape[2] != 3):
        raise ProcessingError("Decoder did not return 16-bit RGB pixels.")
    return rgb, metadata
