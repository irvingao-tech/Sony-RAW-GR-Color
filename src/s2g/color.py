"""An ICC profile with sRGB primaries and a linear transfer function."""

import struct

from PIL import ImageCms


def srgb_profile() -> bytes:
    profile = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    profile[24:36] = struct.pack(">6H", 2000, 1, 1, 0, 0, 0)
    profile[84:100] = bytes(16)
    return bytes(profile)


def linear_srgb_profile() -> bytes:
    # Reuse LittleCMS's D50 colorants, white point and chromatic adaptation.
    # Replace the transfer curves: normal sRGB ICC must not tag linear pixels.
    original = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    count = struct.unpack_from(">I", original, 128)[0]
    tags = []
    label = "S2G Linear sRGB v1".encode("utf-16-be")
    description = b"mluc" + bytes(4) + struct.pack(">II", 1, 12)
    description += b"enUS" + struct.pack(">II", len(label), 28) + label
    for i in range(count):
        sig, offset, size = struct.unpack_from(">4sII", original, 132 + i * 12)
        data = original[offset:offset + size]
        if sig in (b"rTRC", b"gTRC", b"bTRC"):
            data = b"curv" + bytes(8)  # ICC curve count zero means identity.
        elif sig == b"desc":
            data = description
        tags.append((sig, data))
    header = bytearray(original[:128])
    header[24:36] = struct.pack(">6H", 2000, 1, 1, 0, 0, 0)
    header[84:100] = bytes(16)  # Optional profile ID; no stale checksum.
    table = bytearray(struct.pack(">I", count))
    payload = bytearray()
    offset = 132 + count * 12
    for sig, data in tags:
        table.extend(struct.pack(">4sII", sig, offset + len(payload), len(data)))
        payload.extend(data)
        payload.extend(bytes((-len(data)) % 4))
    struct.pack_into(">I", header, 0, offset + len(payload))
    return bytes(header + table + payload)
