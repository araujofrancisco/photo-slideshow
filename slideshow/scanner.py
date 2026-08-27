"""Image discovery and ordering.

Pure, side-effect-free functions so they can be unit-tested with plain
temporary directories and no FFmpeg dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

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
