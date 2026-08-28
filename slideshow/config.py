"""Configuration model and resolution of settings.

Responsibilities (single responsibility principle):
  * Hold validated settings (the Config dataclass).
  * Merge sources with a predictable precedence: CLI > .env > default.
  * Validate the resulting configuration before any work is done.

Why a dataclass + a free `load_config` function (instead of stuffing logic
into argparse)?  It keeps configuration pure and trivially unit-testable
without invoking the CLI, and lets tests construct a Config directly.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigError

# Target resolution must be even for H.264 / yuv420p compatibility.
_RESOLUTION_RE = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")


# Bundled default caption font (permissively licensed DejaVuSans). Resolved at
# import time so it works whether the package is run from source or installed.
DEFAULT_FONT_FILE = Path(__file__).parent / "data" / "default.ttf"


@dataclass
class Config:
    """Resolved, validated runtime settings."""

    input_dir: Path
    output_file: Path
    delay_seconds: float
    transition: str  # "cut" | "crossfade"
    crossfade_seconds: float
    width: int
    height: int
    overwrite: bool
    ken_burns: bool
    encoder: str
    bitrate: str = "auto"  # "auto" or e.g. "8M", "2000k"
    crf: int = 23  # 0-51, lower = higher quality (auto mode uses encoder default)

    # Audio track (optional background music).
    audio_file: Path | None = None
    audio_fade_in: float = 1.0
    audio_fade_out: float = 1.0
    audio_volume: float = 1.0
    audio_loop: bool = False
    audio_normalize: bool = False

    # Per-image options.
    autorotate: bool = True  # honor EXIF orientation so phone photos aren't sideways
    font_file: Path | None = DEFAULT_FONT_FILE  # font used for caption overlays

    def validate(self) -> None:
        """Raise ConfigError if any setting is inconsistent.

        Pure validation only (no side effects such as creating directories).
        """
        if self.delay_seconds <= 0:
            raise ConfigError("delay_seconds must be greater than 0.")
        if self.transition not in ("cut", "crossfade"):
            raise ConfigError(f"transition must be 'cut' or 'crossfade', got {self.transition!r}.")
        if self.crossfade_seconds < 0:
            raise ConfigError("crossfade_seconds must be >= 0.")
        if self.transition == "crossfade" and self.crossfade_seconds >= self.delay_seconds:
            raise ConfigError("crossfade_seconds must be strictly less than delay_seconds.")
        if self.width <= 0 or self.height <= 0:
            raise ConfigError("resolution width and height must be positive.")
        if self.width % 2 != 0 or self.height % 2 != 0:
            raise ConfigError(
                "resolution width and height must be even (required by H.264/yuv420p)."
            )
        if self.crf < 0 or self.crf > 51:
            raise ConfigError("crf must be between 0 and 51.")

        # Audio validation.
        if self.audio_file is not None and not Path(self.audio_file).is_file():
            raise ConfigError(f"audio file not found: {self.audio_file}")
        if self.audio_fade_in < 0:
            raise ConfigError("audio_fade_in must be >= 0.")
        if self.audio_fade_out < 0:
            raise ConfigError("audio_fade_out must be >= 0.")
        if self.audio_volume <= 0:
            raise ConfigError("audio_volume must be greater than 0.")

        # Font validation.
        if self.font_file is not None and not Path(self.font_file).is_file():
            raise ConfigError(f"caption font file not found: {self.font_file}")

        # Directory existence is validated at scan time (scanner.find_images),
        # keeping this method a pure value check with no filesystem side effects.


def _pick(cli_value, env_value, default):
    """Precedence helper: CLI arg wins, then env, then default."""
    if cli_value is not None:
        return cli_value
    if env_value is not None:
        return env_value
    return default


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"expected a number, got {value!r}.") from exc


def _as_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def parse_resolution(value: str) -> tuple[int, int]:
    match = _RESOLUTION_RE.match(value or "")
    if not match:
        raise ConfigError(f"resolution must be 'WIDTHxHEIGHT' (e.g. 1920x1080), got {value!r}.")
    return int(match.group(1)), int(match.group(2))


def _strip_surrounding_quotes(value: str) -> str:
    """Remove a single matching pair of surrounding quotes.

    .env values are frequently quoted when they contain spaces or special
    characters (e.g. paths with spaces, or an apostrophe like "Fountas's").
    python-dotenv strips double quotes but not single quotes, so we normalize
    both here for predictable parsing.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_config(args, env_file: str = ".env") -> Config:
    """Build a validated Config from CLI args + an optional .env file.

    Precedence: explicit CLI flag > .env variable > built-in default.
    """
    load_dotenv(env_file, override=False)

    def env(name, default=None):
        value = os.environ.get(name)
        if value is None:
            return default
        return _strip_surrounding_quotes(value)

    input_dir = _pick(args.input_dir, env("INPUT_DIR"), None)
    output_file = _pick(args.output, env("OUTPUT_FILE"), None)
    delay = _pick(args.delay, _as_float(env("DELAY_SECONDS")), 5.0)
    transition = _pick(args.transition, env("TRANSITION"), "cut")
    crossfade = _pick(args.crossfade, _as_float(env("CROSSFADE_SECONDS")), 1.0)
    resolution = _pick(args.resolution, env("RESOLUTION"), "1920x1080")

    # --overwrite is a store_true flag, so only "True" is observable from CLI.
    # Env can still enable it; CLI can never disable an env-provided True,
    # which is acceptable since the .env is the explicit config file.
    overwrite = bool(args.overwrite) or _as_bool(env("OVERWRITE", "false") or "false")
    ken_burns = bool(args.ken_burns) or _as_bool(env("KEN_BURNS", "false") or "false")
    encoder = _pick(args.encoder, env("ENCODER"), "auto")
    bitrate = _pick(getattr(args, "bitrate", None), env("BITRATE"), "auto")
    crf = int(_pick(getattr(args, "crf", None), _as_float(env("CRF")), 23))

    # Audio (background music) options.
    audio_file = getattr(args, "audio", None) or env("AUDIO_FILE")
    audio_fade_in = (
        _as_float(env("AUDIO_FADE_IN", "1.0"))
        if getattr(args, "audio_fade_in", None) is None
        else args.audio_fade_in
    )
    audio_fade_out = (
        _as_float(env("AUDIO_FADE_OUT", "1.0"))
        if getattr(args, "audio_fade_out", None) is None
        else args.audio_fade_out
    )
    audio_volume = (
        _as_float(env("AUDIO_VOLUME", "1.0"))
        if getattr(args, "audio_volume", None) is None
        else args.audio_volume
    )
    audio_loop = bool(getattr(args, "audio_loop", False)) or _as_bool(
        env("AUDIO_LOOP", "false") or "false"
    )
    audio_normalize = bool(getattr(args, "audio_normalize", False)) or _as_bool(
        env("AUDIO_NORMALIZE", "false") or "false"
    )

    # Per-image options.
    autorotate = not bool(getattr(args, "no_autorotate", False)) and _as_bool(
        env("AUTOROTATE", "true") or "true"
    )
    font_file = _pick(getattr(args, "font_file", None), env("FONT_FILE"), str(DEFAULT_FONT_FILE))

    if input_dir is None:
        raise ConfigError("input directory is required (use --input-dir or set INPUT_DIR in .env).")
    if output_file is None:
        raise ConfigError("output file is required (use --output or set OUTPUT_FILE in .env).")

    width, height = parse_resolution(str(resolution))

    config = Config(
        input_dir=Path(input_dir).expanduser().resolve(),
        output_file=Path(output_file).expanduser().resolve(),
        delay_seconds=float(delay),
        transition=str(transition).strip().lower(),
        crossfade_seconds=float(crossfade),
        width=width,
        height=height,
        overwrite=overwrite,
        ken_burns=ken_burns,
        encoder=str(encoder).strip(),
        bitrate=str(bitrate).strip(),
        crf=crf,
        audio_file=Path(audio_file).expanduser().resolve() if audio_file else None,
        audio_fade_in=float(audio_fade_in),
        audio_fade_out=float(audio_fade_out),
        audio_volume=float(audio_volume),
        audio_loop=audio_loop,
        audio_normalize=audio_normalize,
        autorotate=autorotate,
        font_file=Path(font_file).expanduser().resolve() if font_file else None,
    )
    config.validate()
    return config
