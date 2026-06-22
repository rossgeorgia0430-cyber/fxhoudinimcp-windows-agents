"""Small dependency-light visual assertions for real Houdini GUI captures."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageStat


def image_evidence(path: str | Path) -> dict[str, object]:
    """Decode an image and return stable evidence that it contains pixels."""
    image_path = Path(path)
    with Image.open(image_path) as opened:
        opened.load()
        rgb = opened.convert("RGB")
        width, height = rgb.size
        if width < 64 or height < 64:
            raise AssertionError(f"{image_path} is too small: {width}x{height}")
        extrema = rgb.getextrema()
        ranges = [maximum - minimum for minimum, maximum in extrema]
        if max(ranges) < 3:
            raise AssertionError(f"{image_path} is visually uniform: extrema={extrema}")
        stat = ImageStat.Stat(rgb)
        if max(stat.var) < 1.0:
            raise AssertionError(f"{image_path} has negligible pixel variance: {stat.var}")
        thumb = rgb.resize((16, 16), Image.Resampling.LANCZOS).convert("L")
        values = list(thumb.getdata())
        average = sum(values) / len(values)
        average_hash = "".join("1" if value >= average else "0" for value in values)
        return {
            "width": width,
            "height": height,
            "mode": "RGB",
            "average_hash": average_hash,
            "variance": [round(value, 3) for value in stat.var],
        }


def validate_visual_capture(
    name: str,
    path: str | Path,
    baseline_dir: str | Path | None = None,
    update_baseline: bool = False,
    max_hash_distance: int = 64,
) -> dict[str, object]:
    """Validate pixels, optionally establish/compare a Windows GUI baseline."""
    image_path = Path(path)
    evidence = image_evidence(image_path)
    if baseline_dir is None:
        return evidence

    root = Path(baseline_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    baseline_path = root / f"{name}{image_path.suffix.lower()}"
    if update_baseline:
        shutil.copy2(image_path, baseline_path)
        manifest[name] = evidence
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        evidence["baseline"] = "updated"
        return evidence

    expected = manifest.get(name)
    if expected is None or not baseline_path.exists():
        evidence["baseline"] = "missing"
        return evidence
    if (evidence["width"], evidence["height"]) != (
        expected["width"], expected["height"],
    ):
        raise AssertionError(
            f"{name} dimensions changed: {evidence['width']}x{evidence['height']} "
            f"!= {expected['width']}x{expected['height']}"
        )
    distance = sum(
        left != right
        for left, right in zip(evidence["average_hash"], expected["average_hash"])
    )
    if distance > max_hash_distance:
        raise AssertionError(
            f"{name} perceptual hash distance {distance} exceeds {max_hash_distance}"
        )
    evidence["baseline"] = "matched"
    evidence["hash_distance"] = distance
    return evidence
