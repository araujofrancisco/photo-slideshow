"""Unit tests for the CLI entrypoint."""

from __future__ import annotations

from slideshow.cli import build_arg_parser, main


def test_parser_defaults():
    parser = build_arg_parser()
    args = parser.parse_args([])
    assert args.delay is None
    assert args.transition is None
    assert args.dry_run is False
    assert args.ken_burns is False
    assert args.overwrite is False


def test_parser_with_args():
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--input-dir",
            "/tmp/photos",
            "--output",
            "/tmp/out.mp4",
            "--delay",
            "3",
            "--transition",
            "crossfade",
            "--crossfade",
            "0.5",
            "--resolution",
            "1280x720",
            "--ken-burns",
            "--overwrite",
            "--dry-run",
        ]
    )
    assert args.input_dir == "/tmp/photos"
    assert args.output == "/tmp/out.mp4"
    assert args.delay == 3.0
    assert args.transition == "crossfade"
    assert args.crossfade == 0.5
    assert args.resolution == "1280x720"
    assert args.ken_burns is True
    assert args.overwrite is True
    assert args.dry_run is True


def test_main_no_images_returns_error(monkeypatch, tmp_path):
    """main() returns 1 when input directory has no images."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = main(
        [
            "--input-dir",
            str(empty),
            "--output",
            str(tmp_path / "out.mp4"),
        ]
    )
    assert result == 1


def test_main_dry_run(monkeypatch, tmp_path):
    """dry-run prints the command without executing."""
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"\xff\xd8\xff")  # minimal JPEG header

    output = tmp_path / "out.mp4"
    result = main(
        [
            "--input-dir",
            str(photos),
            "--output",
            str(output),
            "--dry-run",
        ]
    )
    assert result == 0
    assert not output.exists()
