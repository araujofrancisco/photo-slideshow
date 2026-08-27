"""Command-line entrypoint and orchestration.

The CLI wires together config -> scanner -> ffmpeg. It contains no domain
logic of its own (that lives in the other modules), keeping it a thin,
readable composition root.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .errors import SlideshowError
from .ffmpeg import build_command, find_ffmpeg, render, resolve_encoder
from .scanner import find_images

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
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_arg_parser().parse_args(argv)

    try:
        config = load_config(args, args.env_file)
        # Side effect kept at the boundary: ensure the output location exists.
        config.output_file.parent.mkdir(parents=True, exist_ok=True)

        images = find_images(config.input_dir)
        encoder = resolve_encoder(config.encoder, find_ffmpeg())
        LOG.info(
            "Rendering %d image(s): %ss each, transition=%s, encoder=%s -> %s",
            len(images),
            config.delay_seconds,
            config.transition,
            encoder,
            config.output_file,
        )

        if args.dry_run:
            command = build_command(config, images, "ffmpeg", encoder=encoder)
            LOG.info("Dry run — command that would execute:")
            print(" ".join(command))
            return 0

        render(config, images)
        LOG.info("Done: %s", config.output_file)
        return 0

    except SlideshowError as exc:
        LOG.error("Error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
