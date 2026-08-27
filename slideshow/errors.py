"""Typed exceptions for the slideshow tool.

Keeping these in one module lets the CLI map failures to clean, user-facing
error messages instead of leaking stack traces.
"""


class SlideshowError(Exception):
    """Base class for all expected, user-facing failures."""


class FFmpegNotFound(SlideshowError):
    """Raised when the ffmpeg binary is missing from the system PATH."""


class NoImagesFound(SlideshowError):
    """Raised when the input directory contains no supported images."""


class ConfigError(SlideshowError):
    """Raised when configuration is missing or invalid."""


class RenderError(SlideshowError):
    """Raised when an FFmpeg invocation fails."""
