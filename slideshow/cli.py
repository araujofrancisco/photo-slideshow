"""Command-line entrypoint and orchestration.

The CLI wires together config -> scanner -> ffmpeg. It contains no domain
logic of its own (that lives in the other modules), keeping it a thin,
readable composition root.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from tqdm import tqdm

from .config import load_config
from .errors import SlideshowError
from .ffmpeg import build_command, find_ffmpeg, render, resolve_encoder
from .scanner import MediaItem, find_images, scan_items

LOG = logging.getLogger("slideshow")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slideshow",
        description="Turn a folder of images into an MP4 slideshow using FFmpeg.",
    )
    parser.add_argument("-i", "--input-dir", help="Directory containing the images.")
    parser.add_argument("-o", "--output", help="Output MP4 file path.")
    parser.add_argument(
        "-d", "--delay", type=float, help="Seconds each image is shown (default 5)."
    )
    parser.add_argument(
        "-t",
        "--transition",
        choices=["cut", "crossfade"],
        help="Transition between images (default cut).",
    )
    parser.add_argument(
        "-c",
        "--crossfade",
        type=float,
        help="Crossfade duration in seconds when transition=crossfade (default 1).",
    )
    parser.add_argument(
        "-r", "--resolution", help="Target resolution WIDTHxHEIGHT (default 1920x1080)."
    )
    parser.add_argument(
        "-y", "--overwrite", action="store_true", help="Overwrite the output file if it exists."
    )
    parser.add_argument(
        "--ken-burns",
        action="store_true",
        help="Apply a subtle slow zoom/pan (Ken Burns) to each image.",
    )
    parser.add_argument(
        "--encoder",
        default=None,
        help=(
            "Video encoder. Default 'auto' detects the best available hardware "
            "encoder (h264_nvenc > h264_qsv > h264_videotoolbox) and falls back "
            "to libx264. Explicit options: libx264, h264_nvenc (NVIDIA), "
            "h264_qsv (Intel), h264_vaapi (Linux), h264_videotoolbox (macOS)."
        ),
    )
    parser.add_argument(
        "--env-file", default=".env", help="Path to .env config file (default ./.env)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ffmpeg command without executing it.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="JSON file listing per-image {name, duration, caption} in order.",
    )
    parser.add_argument(
        "--durations",
        default=None,
        help="Comma-separated per-image durations in seconds, e.g. '5,4,6'.",
    )
    parser.add_argument(
        "--audio",
        default=None,
        help="Optional background audio file (music) to mux into the video.",
    )
    parser.add_argument(
        "--audio-fade-in",
        type=float,
        default=None,
        help="Audio fade-in duration in seconds (default 1).",
    )
    parser.add_argument(
        "--audio-fade-out",
        type=float,
        default=None,
        help="Audio fade-out duration in seconds (default 1).",
    )
    parser.add_argument(
        "--audio-volume",
        type=float,
        default=None,
        help="Audio volume multiplier (default 1.0).",
    )
    parser.add_argument(
        "--audio-loop",
        action="store_true",
        help="Loop the audio to fill the whole video when it is shorter.",
    )
    parser.add_argument(
        "--audio-normalize",
        action="store_true",
        help="Normalize audio loudness (loudnorm) before mixing.",
    )
    parser.add_argument(
        "--no-autorotate",
        action="store_true",
        help="Disable EXIF auto-orientation (keep images as stored).",
    )
    parser.add_argument(
        "--font-file",
        default=None,
        help="TTF font file used for caption overlays (default: bundled DejaVuSans).",
    )
    return parser


def _build_items(args, config):
    """Resolve the list of MediaItem slides from CLI args + input directory."""
    if args.manifest:
        manifest_path = Path(args.manifest).expanduser()
        if not manifest_path.is_file():
            raise SlideshowError(f"manifest file not found: {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SlideshowError(f"invalid manifest JSON: {exc}") from exc
        if isinstance(manifest, dict):
            manifest = manifest.get("items", [])
        if not isinstance(manifest, list):
            raise SlideshowError("manifest must be a list of items (or {'items': [...]}).")
        return scan_items(config.input_dir, manifest, default_duration=config.delay_seconds)

    if args.durations:
        try:
            durations = [float(x) for x in str(args.durations).split(",") if x.strip() != ""]
        except ValueError as exc:
            raise SlideshowError(f"invalid --durations: {exc}") from exc
        images = find_images(config.input_dir)
        if len(durations) != len(images):
            raise SlideshowError(
                f"--durations listed {len(durations)} values but found {len(images)} images."
            )
        return [MediaItem(path=p, duration=d) for p, d in zip(images, durations, strict=True)]

    return scan_items(config.input_dir, default_duration=config.delay_seconds)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_arg_parser().parse_args(argv)

    try:
        config = load_config(args, args.env_file)
        # Side effect kept at the boundary: ensure the output location exists.
        config.output_file.parent.mkdir(parents=True, exist_ok=True)

        items = _build_items(args, config)
        encoder = resolve_encoder(config.encoder, find_ffmpeg())
        LOG.info(
            "Rendering %d image(s): %ss each, transition=%s, encoder=%s -> %s",
            len(items),
            config.delay_seconds,
            config.transition,
            encoder,
            config.output_file,
        )
        if config.audio_file is not None:
            LOG.info("Background audio: %s", config.audio_file)

        if args.dry_run:
            command = build_command(config, items, "ffmpeg", encoder=encoder)
            LOG.info("Dry run — command that would execute:")
            print(" ".join(command))
            return 0

        def _progress(pct: float) -> None:
            bar.update(max(0.0, pct - bar.n))

        bar = tqdm(total=100, desc="Rendering", unit="%", unit_scale=False)
        try:
            render(config, items, progress_callback=_progress)
        finally:
            bar.close()
        LOG.info("Done: %s", config.output_file)
        return 0

    except SlideshowError as exc:
        LOG.error("Error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
