"""QA preview only: encode a linear TIFF as an sRGB JPEG for inspection."""
import argparse
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageCms
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from s2g.color import linear_srgb_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Preview already exists; choose another output name.")
    with tifffile.TiffFile(args.source) as tif:
        page = tif.pages[0]
        if page.tags[34675].value != linear_srgb_profile():
            parser.error("Expected an S2G linear-sRGB TIFF.")
        rgb = page.asarray()
    # Resample in linear light, then use sRGB's exact transfer function.
    width = min(1600, rgb.shape[1])
    height = round(rgb.shape[0] * width / rgb.shape[1])
    channels = []
    for c in range(3):
        channel = Image.fromarray(rgb[:, :, c].astype(np.float32))
        channels.append(np.asarray(channel.resize((width, height), Image.Resampling.LANCZOS)))
    linear = np.clip(np.stack(channels, axis=2) / 65535.0, 0, 1)
    encoded = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)
    preview = Image.fromarray(np.round(encoded * 255).astype(np.uint8))
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as output:
        preview.save(output, format="JPEG", quality=95, icc_profile=icc)
    print(f"QA preview: {args.output.resolve()} ({width} x {height})")


if __name__ == "__main__":
    main()
