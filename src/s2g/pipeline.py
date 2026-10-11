"""One tiled float32 scene graph for previews, JPEG and 16-bit sRGB TIFF."""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .edits import Edits, PROFILE_ID
from .raw import ProcessingError

RESOURCES = Path(__file__).parent / "resources"
PROFILE_BYTES = (RESOURCES / "sony_a7c2_libraw_v1.json").read_bytes()
LOOK_BYTES = (RESOURCES / "looks_v1.json").read_bytes()
PROFILE = json.loads(PROFILE_BYTES)
LOOKS = json.loads(LOOK_BYTES)["looks"]
PROFILE_SHA = hashlib.sha256(PROFILE_BYTES).hexdigest()
LOOK_SHA = hashlib.sha256(LOOK_BYTES).hexdigest()
LOOK_V2_BYTES = (RESOURCES / "looks_v2.json").read_bytes()
LOOK_V2_SHA = hashlib.sha256(LOOK_V2_BYTES).hexdigest()
LOOKS.update(json.loads(LOOK_V2_BYTES)["looks"])


def look_sha(edit):
    return LOOK_V2_SHA if edit.look.endswith("_v2") else LOOK_SHA


def engine_identity(edit):
    # Frozen v1 renderer and parameter bytes keep existing edits reproducible.
    result = {"version": "0.3.0" if edit.look.endswith("_v2") else "0.2.0",
            "profile": PROFILE_ID, "profile_sha256": PROFILE_SHA,
            "look_parameters_sha256": look_sha(edit)}
    if edit.color_controls_active:
        from .color_controls import PARAMETERS_SHA
        result.update(base_version=result["version"], version="0.4.0",
                      color_controls_sha256=PARAMETERS_SHA)
    return result


def xyz_white(xy):
    x, y = xy
    return np.array([x / y, 1, (1 - x - y) / y], dtype=np.float64)


def rgb_to_xyz(primaries, white):
    basis = np.column_stack([xyz_white(xy) for xy in primaries])
    return basis @ np.diag(np.linalg.solve(basis, xyz_white(white)))


D65 = (.3127, .3290)
ACES_WHITE = (.32168, .33767)
SRGB_TO_XYZ = rgb_to_xyz(((.64, .33), (.30, .60), (.15, .06)), D65)
AP1_TO_XYZ = rgb_to_xyz(((.713, .293), (.165, .830), (.128, .044)), ACES_WHITE)
BRADFORD = np.array([[.8951, .2664, -.1614], [-.7502, 1.7135, .0367], [.0389, -.0685, 1.0296]])


def adaptation(source, target):
    gain = (BRADFORD @ xyz_white(target)) / (BRADFORD @ xyz_white(source))
    return np.linalg.inv(BRADFORD) @ np.diag(gain) @ BRADFORD


CAM_XYZ = np.asarray(PROFILE["xyz_d65_to_camera"], dtype=np.float64)
camera_from_srgb = CAM_XYZ @ SRGB_TO_XYZ
camera_from_srgb /= camera_from_srgb.sum(axis=1, keepdims=True)
CAMERA_TO_XYZ = SRGB_TO_XYZ @ np.linalg.inv(camera_from_srgb)
CAMERA_TO_AP1 = (np.linalg.inv(AP1_TO_XYZ) @ adaptation(D65, ACES_WHITE) @ CAMERA_TO_XYZ).astype(np.float32)
AP1_TO_SRGB = (np.linalg.inv(SRGB_TO_XYZ) @ adaptation(ACES_WHITE, D65) @ AP1_TO_XYZ).astype(np.float32)
AP1_LUMA = AP1_TO_XYZ[1].astype(np.float32)
SRGB_LUMA = SRGB_TO_XYZ[1].astype(np.float32)


def white_balance(metadata, edit: Edits):
    if edit.temperature is None:
        wb = np.asarray(metadata["as_shot_white_balance"][:3], dtype=np.float32)
    else:
        # Planckian-locus approximation, not a measured camera Kelvin calibration.
        t = edit.temperature
        if t <= 4000:
            x = -.2661239e9 / t**3 - .2343580e6 / t**2 + .8776956e3 / t + .179910
        else:
            x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + .2226347e3 / t + .240390
        if t <= 2222:
            y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - .20219683
        elif t <= 4000:
            y = -.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - .16748867
        else:
            y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - .37001483
        neutral = CAM_XYZ @ xyz_white((x, y))
        if (neutral <= 0).any():
            raise ProcessingError("Temperature produces invalid camera white balance.")
        wb = (1 / neutral).astype(np.float32)
    wb /= wb[1]
    wb[[0, 2]] *= np.float32(2 ** (edit.tint / 100))
    return wb


def scene_transform(camera: np.ndarray, metadata: dict, edit: Edits):
    # LibRaw has already subtracted black before unscaled demosaic.
    black = np.asarray(metadata["black_level_per_channel"][:3], dtype=np.float32)
    scale = np.float32(metadata["white_level"]) - black
    if (scale <= 0).any():
        raise ProcessingError("Invalid sensor normalization levels.")
    observed = np.asarray(metadata["camera_rgb_to_xyz_matrix"][:3])
    if not np.allclose(observed, CAM_XYZ, atol=2e-5, rtol=0):
        raise ProcessingError("RAW camera matrix differs from the versioned profile.")
    linear = camera.astype(np.float32) / scale
    linear *= white_balance(metadata, edit)
    # No clipping: signed wide-gamut colors and values over 1 survive this stage.
    return linear @ CAMERA_TO_AP1.T


def display_graph(acescg: np.ndarray, edit: Edits):
    encoded = display_base_graph(acescg, edit)
    if edit.saturation != 0 or edit.vibrance != 0:
        from .color_controls import adjust_color
        encoded = adjust_color(encoded, edit)
    return encoded


def display_base_graph(acescg: np.ndarray, edit: Edits):
    if edit.look.endswith("_v2") and edit.strength > 0:
        from .appearance import display_v2
        return display_v2(acescg, edit, LOOKS[edit.look])
    if edit.look.endswith("_v2"):
        from dataclasses import replace
        return display_base_graph(acescg, replace(edit, look="neutral_v1"))
    look = LOOKS[edit.look]
    strength = edit.strength if edit.look != "neutral_v1" else 0.0
    rgb = acescg * np.float32(2 ** (edit.exposure_ev + look["exposure"] * strength))
    luma = rgb @ AP1_LUMA
    safe = np.maximum(luma, np.float32(1e-8))
    log_luma = np.log2(safe)
    target = np.interp(log_luma, look["tone_x"], look["tone_y"]).astype(np.float32)
    new_luma = np.exp2(log_luma + strength * (target - log_luma))
    low = np.exp(-safe / np.float32(.12))
    high = safe / (safe + np.float32(.5))
    new_luma *= np.exp2(np.float32(edit.shadows / 100) * low - np.float32(edit.highlights / 100) * high)
    ratio = np.where(luma > 1e-8, new_luma / safe, 1).astype(np.float32)
    rgb *= ratio[..., None]
    luma = rgb @ AP1_LUMA
    chroma = rgb - luma[..., None]
    rgb = luma[..., None] + chroma * np.float32(1 + strength * (look["saturation"] - 1))
    chromatic = np.clip(np.max(np.abs(chroma), axis=-1) / (np.maximum(luma, 0) + .08), 0, 1)
    # Protect near-neutrals and a broad warm skin-like sector from split toning.
    warm = (chroma[..., 0] > 0) & (chroma[..., 2] < 0)
    protection = chromatic * np.where(warm, .2, 1).astype(np.float32)
    shadow = np.asarray(look["shadow_tint"], dtype=np.float32)
    highlight = np.asarray(look["highlight_tint"], dtype=np.float32)
    shadow -= shadow @ AP1_LUMA
    highlight -= highlight @ AP1_LUMA
    tint_rgb = strength * protection[..., None] * (low[..., None] * shadow + high[..., None] * highlight)
    if edit.split_toning != 100:
        tint_rgb *= np.float32(edit.split_toning / 100)
    rgb += tint_rgb
    linear_srgb = rgb @ AP1_TO_SRGB.T
    y = np.maximum(linear_srgb @ SRGB_LUMA, 0)
    # Identity below .7, smooth monotonic shoulder above; this is a custom SDR
    # display transform, not an official ACES output transform.
    mapped_y = np.where(y <= .7, y, .7 + .3 * (-np.expm1(-np.maximum(y - .7, 0) / .3)))
    linear_srgb *= np.where(y > 1e-8, mapped_y / np.maximum(y, 1e-8), 1)[..., None]
    # Compress chroma toward mapped luminance to fit the output gamut.
    delta = linear_srgb - mapped_y[..., None]
    upper = (1 - mapped_y[..., None]) / np.maximum(delta, 1e-8)
    lower = mapped_y[..., None] / np.maximum(-delta, 1e-8)
    allowance = np.where(delta > 0, upper, np.where(delta < 0, lower, 1))
    amount = np.minimum(1, np.maximum(0, np.min(allowance, axis=-1)))
    linear_srgb = mapped_y[..., None] + delta * amount[..., None]
    lift = np.float32(look["black_lift"] * strength)
    linear_srgb = np.clip(linear_srgb * (1 - lift) + lift, 0, 1)
    return np.where(linear_srgb <= .0031308, linear_srgb * 12.92,
                    1.055 * np.maximum(linear_srgb, 0) ** (1 / 2.4) - .055).astype(np.float32)


def geometry(camera, edit: Edits):
    height, width = camera.shape[:2]
    if edit.crop is not None:
        x, y, w, h = edit.crop
        x0, y0 = int(round(x * width)), int(round(y * height))
        x1, y1 = int(round((x + w) * width)), int(round((y + h) * height))
        if x1 <= x0 or y1 <= y0:
            raise ProcessingError("Crop is too small at this resolution.")
        camera = camera[y0:y1, x0:x1]
    if edit.rotation:
        camera = np.rot90(camera, k=-edit.rotation // 90)
    return camera


def resize_camera(camera, max_size):
    if max_size is None or max(camera.shape[:2]) <= max_size:
        return camera
    h, w = camera.shape[:2]
    scale = max_size / max(h, w)
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    # Demosaiced sensor counts are linear: resample before the same pixel graph.
    result = np.empty((size[1], size[0], 3), dtype=np.float32)
    for c in range(3):
        channel = Image.fromarray(camera[..., c].astype(np.float32))
        result[..., c] = np.asarray(channel.resize(size, Image.Resampling.LANCZOS))
    return result


def render_pixels(camera, metadata, edit: Edits, *, bits=8, max_size=None, progress=None, cancelled=None):
    edit = Edits.from_dict(edit.to_dict())
    if bits not in (8, 16):
        raise ProcessingError("Output precision must be 8 or 16 bits.")
    camera = resize_camera(geometry(camera, edit), max_size)
    h, w = camera.shape[:2]
    result = np.empty((h, w, 3), dtype=np.uint8 if bits == 8 else np.uint16)
    rng = np.random.Generator(np.random.PCG64(edit.seed))
    for y in range(0, h, 128):
        if cancelled and cancelled():
            raise ProcessingError("Export cancelled.")
        scene = scene_transform(camera[y:y + 128], metadata, edit)
        encoded = display_graph(scene, edit)
        if edit.grain:
            luma = encoded @ SRGB_LUMA
            amplitude = np.float32(edit.grain / 100 * .025) * np.sqrt(np.maximum(luma * (1 - luma), 0))
            noise = rng.standard_normal(luma.shape, dtype=np.float32) * amplitude
            encoded = np.clip(encoded + noise[..., None], 0, 1)
        result[y:y + 128] = np.rint(encoded * (255 if bits == 8 else 65535)).astype(result.dtype)
        if progress:
            progress(min(y + 128, h) / h)
    return result
