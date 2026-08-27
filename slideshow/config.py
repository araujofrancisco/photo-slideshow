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
    )
    config.validate()
    return config
