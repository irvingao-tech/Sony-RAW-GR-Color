"""V2 appearance: smooth scene tone, linear matrix, perceptual chroma.

All look parameters are authored approximations, not fitted GR measurements.
Oklab equations: Bjorn Ottosson, public-domain implementation (see credits).
"""
from functools import lru_cache

import numpy as np

LMS = np.array([[.4122214708, .5363325363, .0514459929],
                [.2119034982, .6806995451, .1073969566],
                [.0883024619, .2817188376, .6299787005]], dtype=np.float32)
LAB = np.array([[.2104542553, .7936177850, -.0040720468],
                [1.9779984951, -2.4285922050, .4505937099],
                [.0259040371, .7827717662, -.8086757660]], dtype=np.float32)
INV_LMS = np.linalg.inv(LMS).astype(np.float32)
INV_LAB = np.linalg.inv(LAB).astype(np.float32)


def to_oklab(rgb):
    return np.cbrt(rgb @ LMS.T) @ LAB.T


def from_oklab(lab):
    return ((lab @ INV_LAB.T) ** 3) @ INV_LMS.T


@lru_cache(maxsize=8)
def tone_table(x_tuple, y_tuple):
    """1024 float samples of a monotone cubic Hermite curve in log2 exposure.

    Positive secants, harmonic interior slopes and limited endpoints prevent
    ringing/overshoot. Unit slope extrapolation preserves extreme headroom.
    """
    x, y = np.array(x_tuple), np.array(y_tuple)
    h, d = np.diff(x), np.diff(y) / np.diff(x)
    if not ((h > 0).all() and (d > 0).all()):
        raise ValueError("Tone knots must increase strictly.")
    m = np.empty_like(x)
    m[0], m[-1] = d[0], d[-1]
    w1, w2 = 2*h[1:] + h[:-1], h[1:] + 2*h[:-1]
    m[1:-1] = (w1 + w2) / (w1/d[:-1] + w2/d[1:])
    grid = np.linspace(x[0], x[-1], 1024)
    i = np.clip(np.searchsorted(x, grid, side="right")-1, 0, len(x)-2)
    t = (grid-x[i])/h[i]
    result = ((2*t**3-3*t**2+1)*y[i] + (t**3-2*t**2+t)*h[i]*m[i]
              + (-2*t**3+3*t**2)*y[i+1] + (t**3-t**2)*h[i]*m[i+1])
    return grid, result


def tone_map(log_y, look):
    x, y = tone_table(tuple(look["tone_x"]), tuple(look["tone_y"]))
    result = np.interp(log_y, x, y)
    result = np.where(log_y < x[0], log_y + y[0]-x[0], result)
    result = np.where(log_y > x[-1], log_y + y[-1]-x[-1], result)
    return result.astype(np.float32)


def neutral_luma_matrix(look):
    from .pipeline import AP1_TO_SRGB, AP1_LUMA
    matrix = np.array(look["matrix_linear_srgb"], dtype=np.float64)
    matrix /= matrix.sum(axis=1, keepdims=True)
    matrix = np.linalg.inv(AP1_TO_SRGB.astype(np.float64)) @ matrix @ AP1_TO_SRGB
    # Preserve both the neutral axis and luminance. Matrix influences chroma.
    matrix += np.outer(np.ones(3), AP1_LUMA - AP1_LUMA @ matrix)
    return matrix.astype(np.float32)


def hue_weight(hue, center, width):
    distance = (hue-center+180) % 360 - 180
    return np.exp(-.5*(distance/width)**2)


def perceptual_color(linear, look, strength, split_toning=100):
    lab = to_oklab(linear)
    light = np.clip(lab[..., 0], 0, 1)
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1]))
    # Continuous warm-color mask; heuristic protection, not face detection.
    skin = hue_weight(hue, 55, 24) * np.exp(-((chroma-.09)/.1)**2)
    skin *= np.clip((light-.15)/.2, 0, 1) * np.clip((.95-light)/.15, 0, 1)
    protect = 1-look["skin_protection"]*skin
    chromatic = np.clip((chroma-.006)/.045, 0, 1)
    mid = np.clip(4*light*(1-light), 0, 1)
    saturation = np.full_like(light, look["saturation"]-1)
    shift = np.zeros_like(light)
    for center, width, degrees, scale in look["hue_sectors"]:
        weight = hue_weight(hue, center, width)
        saturation += weight*(scale-1)
        shift += weight*degrees
    c = chroma * (1 + strength*protect*saturation*mid)
    angle = np.radians(hue + strength*protect*chromatic*shift*mid)
    lab[..., 1], lab[..., 2] = c*np.cos(angle), c*np.sin(angle)
    shadow = (1-light)**3
    highlight = light**3
    tint = (shadow[..., None]*np.asarray(look["shadow_ab"], np.float32)
            + highlight[..., None]*np.asarray(look["highlight_ab"], np.float32))
    if split_toning != 100:
        tint *= np.float32(split_toning / 100)
    lab[..., 1:] += strength*(protect*chromatic*mid)[..., None]*tint
    return from_oklab(lab)


def fit_gamut(linear):
    from .pipeline import SRGB_LUMA
    y = np.clip(linear @ SRGB_LUMA, 0, 1)
    delta = linear-y[..., None]
    upper = (1-y[..., None])/np.maximum(delta, 1e-8)
    lower = y[..., None]/np.maximum(-delta, 1e-8)
    allowance = np.where(delta > 0, upper, np.where(delta < 0, lower, 1))
    amount = np.clip(np.min(allowance, axis=-1), 0, 1)
    return np.clip(y[..., None]+delta*amount[..., None], 0, 1)


def display_v2(scene, edit, look):
    from .pipeline import AP1_LUMA, AP1_TO_SRGB, SRGB_LUMA
    s = edit.strength
    rgb = scene*np.float32(2**(edit.exposure_ev+look["exposure"]*s))
    mixed = rgb @ neutral_luma_matrix(look).T
    rgb += np.float32(s*look["matrix_mix"])*(mixed-rgb)
    y = rgb @ AP1_LUMA
    safe = np.maximum(y, 1e-8)
    log_y = np.log2(safe)
    target = np.exp2(log_y+s*(tone_map(log_y, look)-log_y))
    low, high = np.exp(-safe/.12), safe/(safe+.5)
    target *= np.exp2(edit.shadows/100*low-edit.highlights/100*high)
    rgb *= np.where(y > 1e-8, target/safe, 1)[..., None]
    linear = rgb @ AP1_TO_SRGB.T
    y = np.maximum(linear @ SRGB_LUMA, 0)
    mapped = np.where(y <= .7, y, .7+.3*(-np.expm1(-np.maximum(y-.7, 0)/.3)))
    linear *= np.where(y > 1e-8, mapped/np.maximum(y, 1e-8), 1)[..., None]
    # Gamut fit before Oklab avoids near-black negative-luminance excursions.
    linear = fit_gamut(perceptual_color(fit_gamut(linear), look, s, edit.split_toning))
    encoded = np.where(linear <= .0031308, linear*12.92,
                       1.055*np.maximum(linear, 0)**(1/2.4)-.055)
    # A small perceptual toe, confined to shadows; avoids linear-lift gray wash.
    encoded += np.float32(s*look["black_lift_encoded"])*(1-encoded)**4
    return np.clip(encoded, 0, 1).astype(np.float32)
