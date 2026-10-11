"""Validated, portable edits. Source and engine identity are enforced on load."""
from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
import tempfile

from .raw import ProcessingError, sha256, validate_source

PROFILE_ID = "sony_a7c2_libraw_v1"
LOOK_IDS = ("neutral_v1", "gr_positive_experimental_v2", "gr_negative_experimental_v2",
            "gr_positive_experimental_v1", "gr_negative_experimental_v1")


@dataclass(frozen=True)
class Edits:
    look: str = "neutral_v1"
    strength: float = 0.82
    exposure_ev: float = 0.0
    temperature: float | None = None
    tint: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    saturation: float = 0.0
    vibrance: float = 0.0
    split_toning: float = 100.0
    grain: float = 0.0
    seed: int = 42
    crop: tuple | None = None
    rotation: int = 0

    @classmethod
    def from_dict(cls, data: dict):
        if not isinstance(data, dict):
            raise ProcessingError("Edits must be a JSON object.")
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ProcessingError(f"Unknown edit fields: {', '.join(sorted(unknown))}")
        edit = cls(**data)
        if edit.look not in LOOK_IDS:
            raise ProcessingError("Unknown look version.")
        ranges = {"strength": (0, 1), "exposure_ev": (-5, 5), "tint": (-100, 100),
                  "highlights": (-100, 100), "shadows": (-100, 100), "grain": (0, 100),
                  "saturation": (-100, 100), "vibrance": (-100, 100), "split_toning": (0, 200)}
        if edit.temperature is not None:
            ranges["temperature"] = (2000, 12000)
        for key, (lo, hi) in ranges.items():
            value = getattr(edit, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
                raise ProcessingError(f"{key} must be between {lo} and {hi}.")
        if type(edit.seed) is not int or not 0 <= edit.seed < 2**32:
            raise ProcessingError("Grain seed must be a 32-bit unsigned integer.")
        if type(edit.rotation) is not int or edit.rotation not in (0, 90, 180, 270):
            raise ProcessingError("Rotation must be 0, 90, 180 or 270 degrees clockwise.")
        if edit.crop is not None:
            c = edit.crop
            if not isinstance(c, (tuple, list)) or len(c) != 4 or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in c
            ):
                raise ProcessingError("Crop must contain normalized x, y, width, height.")
            x, y, w, h = c
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.00000001 or y + h > 1.00000001:
                raise ProcessingError("Crop must stay within the image.")
        return edit

    def to_dict(self):
        result = asdict(self)
        # Omit inactive new controls so unmodified edits retain the old format.
        for key, default in (("saturation", 0), ("vibrance", 0), ("split_toning", 100)):
            if result[key] == default:
                del result[key]
        return result

    @property
    def color_controls_active(self):
        return self.saturation != 0 or self.vibrance != 0 or self.split_toning != 100


def sidecar_path(source: Path) -> Path:
    return source.with_suffix(source.suffix + ".s2g.json")


def sidecar_document(source: Path, edit: Edits, source_hash: str | None = None) -> dict:
    from .pipeline import engine_identity
    edit = Edits.from_dict(edit.to_dict())
    return {
        "schema": 1,
        "source": {"sha256": source_hash or sha256(source), "camera": "ILCE-7CM2"},
        "engine": engine_identity(edit),
        "look": {"id": edit.look, "strength": edit.strength},
        "edits": edit.to_dict(),
        "output": {"space": "sRGB", "intent": "relative_colorimetric"},
    }


def save_sidecar(source: Path, edit: Edits) -> dict:
    source = validate_source(source)
    from .raw import camera_identity
    camera_identity(source)
    document = sidecar_document(source, edit)
    target = sidecar_path(source)
    if target.is_symlink():
        raise ProcessingError("Refusing to replace a symlink sidecar.")
    fd, name = tempfile.mkstemp(prefix=".s2g-edit-", suffix=".json", dir=source.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if sha256(source) != document["source"]["sha256"]:
            raise ProcessingError("Source changed while saving edits.")
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    return document


def load_sidecar(source: Path) -> Edits:
    from .pipeline import engine_identity
    path = sidecar_path(source)
    if not path.exists():
        return Edits()
    if path.stat().st_size > 1024 * 1024:
        raise ProcessingError("Sidecar exceeds 1 MB.")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or type(doc.get("schema")) is not int or doc["schema"] != 1:
            raise ProcessingError("Unsupported sidecar schema.")
        if doc["source"]["camera"] != "ILCE-7CM2" or doc["source"]["sha256"] != sha256(source):
            raise ProcessingError("Sidecar belongs to a different or changed RAW file.")
        edit = Edits.from_dict(doc["edits"])
        if doc["engine"] != engine_identity(edit):
            raise ProcessingError("Sidecar engine/profile version requires an explicit migration.")
        if doc["look"] != {"id": edit.look, "strength": edit.strength}:
            raise ProcessingError("Sidecar look fields disagree.")
        return edit
    except (ValueError, KeyError, TypeError) as exc:
        raise ProcessingError(f"Invalid sidecar: {exc}") from exc
