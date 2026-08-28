"""Image discovery and ordering.

Pure, side-effect-free functions so they can be unit-tested with plain
temporary directories and no FFmpeg dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import NoImagesFound

# Formats FFmpeg can decode out of the box on a standard build.
# (HEIC/HEIF require the non-free libavif/heif and are intentionally excluded
#  to avoid surprising failures; document as a known limitation.)
SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".gif",
}


@dataclass
class MediaItem:
    """A single slide: its image file plus optional per-image overrides.

    * ``duration`` is how long the image stays on screen (seconds). It
      defaults to the global ``delay_seconds`` when built from a plain scan.
    * ``caption`` is an optional text overlay burned onto the image.
    """

    path: Path
    duration: float = 5.0
    caption: str | None = None


def _natural_key(text: str):
    """Split a string into text/digit chunks so numbers sort numerically.

    'img2' < 'img10' (lexicographic sort would put 'img10' before 'img2').
    """
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def find_images(directory: Path) -> list[Path]:
    """Return supported image files in *directory*, naturally sorted by name.

    Raises NoImagesFound when the path is not a directory or contains no
    usable images, giving the CLI a clean, actionable error message.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NoImagesFound(f"not a directory: {directory}")

    images = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not images:
        raise NoImagesFound(
            f"no supported images found in {directory} "
            f"(supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
        )

    images.sort(key=lambda p: _natural_key(p.name))
    return images


def scan_items(
    directory: Path,
    manifest: list[dict[str, Any]] | None = None,
    default_duration: float = 5.0,
) -> list[MediaItem]:
    """Discover images and return :class:`MediaItem` objects (ordered).

    When ``manifest`` is provided (a list of ``{"name", "duration",
    "caption"}`` dicts), the returned items follow that order and pick up the
    per-image ``duration``/``caption`` overrides. Names in the manifest that
    are not present on disk are skipped; files on disk not listed in the
    manifest are ignored (so the manifest fully drives ordering/content).

    Without a manifest, all supported images are returned in natural-sorted
    order, each with ``duration=default_duration`` and no caption.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NoImagesFound(f"not a directory: {directory}")

    if manifest is None:
        return [MediaItem(path=p, duration=default_duration) for p in find_images(directory)]

    by_name = {p.name: p for p in directory.iterdir() if p.is_file()}
    items: list[MediaItem] = []
    for entry in manifest:
        name = entry.get("name")
        if not name or name not in by_name:
            continue
        try:
            duration = float(entry.get("duration", default_duration))
        except (TypeError, ValueError):
            duration = default_duration
        caption = entry.get("caption")
        items.append(MediaItem(path=by_name[name], duration=duration, caption=caption))
    if not items:
        raise NoImagesFound(f"no manifest entries matched files in {directory}")
    return items
