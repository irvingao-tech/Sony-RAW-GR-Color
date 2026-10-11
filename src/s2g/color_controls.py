"""Independent perceptual color controls with a frozen zero-adjustment path."""
import hashlib
import json
from pathlib import Path

import numpy as np

from .appearance import from_oklab, hue_weight, to_oklab

PARAMETERS_BYTES = (Path(__file__).parent / "resources" / "color_controls_v1.json").read_bytes()
PARAMETERS_SHA = hashlib.sha256(PARAMETERS_BYTES).hexdigest()
PARAMETERS = json.loads(PARAMETERS_BYTES)


def decode_srgb(encoded):
    return np.where(encoded <= .04045, encoded/12.92,
                    ((np.maximum(encoded, 0)+.055)/1.055)**2.4).astype(np.float32)


def encode_srgb(linear):
    return np.where(linear <= .0031308, linear*12.92,
                    1.055*np.maximum(linear, 0)**(1/2.4)-.055).astype(np.float32)


def fit_oklab_gamut(lab):
    """Reduce chroma at fixed Oklab L/h until linear sRGB is in gamut.

    Only out-of-gamut pixels enter the search; no image-wide statistics or
    tile-dependent processing. Tiny matrix roundoff is tolerated then clipped.
    """
    linear = from_oklab(lab)
    invalid = np.any((linear < -2e-7) | (linear > 1.0000002), axis=-1)
    if invalid.any():
        selected = lab[invalid].copy()
        selected[:, 0] = np.clip(selected[:, 0], 0, 1)
        chroma = selected[:, 1:].copy()
        lo, hi = np.zeros(len(selected), np.float32), np.ones(len(selected), np.float32)
        for _ in range(PARAMETERS["gamut_bisections"]):
            mid = (lo+hi)*.5
            selected[:, 1:] = chroma*mid[:, None]
            candidate = from_oklab(selected)
            valid = np.all((candidate >= -2e-7) & (candidate <= 1.0000002), axis=-1)
            lo = np.where(valid, mid, lo)
            hi = np.where(valid, hi, mid)
        selected[:, 1:] = chroma*lo[:, None]
        linear[invalid] = from_oklab(selected)
    return np.clip(linear, 0, 1)


def adjust_color(encoded, edit):
    if edit.saturation == 0 and edit.vibrance == 0:
        return encoded
    lab = to_oklab(decode_srgb(encoded))
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    gain = np.full_like(chroma, 1+edit.saturation/100)
    if edit.vibrance != 0:
        hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1]))
        skin = hue_weight(hue, PARAMETERS["skin_hue_degrees"], PARAMETERS["skin_hue_width"])
        skin *= np.exp(-((chroma-PARAMETERS["skin_chroma_center"])/PARAMETERS["skin_chroma_width"])**2)
        weak = np.exp(-(chroma/PARAMETERS["vibrance_chroma_scale"])**2)
        gain *= 1 + (edit.vibrance/100*PARAMETERS["vibrance_gain"])*weak*(1-PARAMETERS["skin_protection"]*skin)
    lab[..., 1:] *= gain[..., None]
    # Neutral conversion avoids importing tiny numerical chroma into grayscale.
    if edit.saturation == -100:
        lab[..., 1:] = 0
    return encode_srgb(fit_oklab_gamut(lab))
