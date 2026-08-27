"""Photo Slideshow → MP4 generator.

A small, modular CLI tool that turns a folder of images into an MP4 video
using system FFmpeg. Per-image delay is configurable (default 5s) and the
tool supports both hard cuts and crossfade transitions.

Architecture (SOLID / DRY):
    cli.py       -> orchestration & argument parsing (single responsibility)
    config.py    -> Config data model + .env/CLI merging + validation
    scanner.py   -> image discovery & natural sort (pure, testable)
    ffmpeg.py    -> command construction & execution (decoupled from I/O)
    errors.py    -> typed exceptions for clean UX

Config precedence: CLI argument > .env value > built-in default.
"""

from .cli import main

__version__ = "1.0.0"
__all__ = ["main"]
