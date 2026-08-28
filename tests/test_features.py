"""Tests for the new features: manifest/per-item options, EXIF autorotate,
caption overlays, audio track, and real cancellation."""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from slideshow.config import Config
from slideshow.errors import CancelError, ConfigError
from slideshow.ffmpeg import build_command, render
from slideshow.scanner import MediaItem, scan_items


def _config(**overrides):
    base = dict(
        input_dir=Path("/in"),
        output_file=Path("/out.mp4"),
        delay_seconds=5.0,
        transition="cut",
        crossfade_seconds=1.0,
        width=1920,
        height=1080,
        overwrite=False,
        ken_burns=False,
        encoder="libx264",
    )
    base.update(overrides)
    return Config(**base)


# --------------------------------------------------------------------------- #
# scanner: manifest / per-item
# --------------------------------------------------------------------------- #
def test_scan_items_manifest_orders_and_overrides(tmp_path):
    (tmp_path / "b.png").write_bytes(b"x")
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "c.png").write_bytes(b"x")
    manifest = [
        {"name": "c.png", "duration": 7, "caption": "third"},
        {"name": "a.png", "duration": 2},
    ]
    items = scan_items(tmp_path, manifest, default_duration=5.0)
    assert [it.path.name for it in items] == ["c.png", "a.png"]
    assert items[0].duration == 7
    assert items[0].caption == "third"
    assert items[1].duration == 2
    assert items[1].caption is None


def test_scan_items_manifest_skips_unmatched(tmp_path):
    from slideshow.errors import NoImagesFound

    (tmp_path / "a.png").write_bytes(b"x")
    # No manifest entry matches a real file -> treated as "no usable images".
    import pytest

    with pytest.raises(NoImagesFound):
        scan_items(tmp_path, [{"name": "missing.png"}], default_duration=5.0)


def test_scan_items_default_uses_global_delay(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.png").write_bytes(b"x")
    items = scan_items(tmp_path, default_duration=4.0)
    assert [it.duration for it in items] == [4.0, 4.0]


# --------------------------------------------------------------------------- #
# EXIF autorotate
# --------------------------------------------------------------------------- #
def _make_oriented(tmp_path, orientation):
    p = tmp_path / "oriented.png"
    img = Image.new("RGB", (60, 40), (10, 20, 30))
    exif = img.getexif()
    exif[0x0112] = orientation
    img.save(p, exif=exif)
    return p


def test_transpose_filter_for_orientation_6(tmp_path):
    from slideshow.ffmpeg import _transpose_filter

    # orientation 6 -> 90° CW -> transpose=1
    assert _transpose_filter(_make_oriented(tmp_path, 6), True) == "transpose=1"


def test_transpose_disabled_when_autorotate_false():
    from slideshow.ffmpeg import _transpose_filter

    # File existence isn't checked; just ensure None is returned when disabled.
    assert _transpose_filter(Path("/nope.png"), False) is None


def test_build_command_includes_transpose_for_oriented(tmp_path):
    oriented = _make_oriented(tmp_path, 6)
    cfg = _config(autorotate=True)
    cmd = build_command(cfg, [MediaItem(path=oriented, duration=2)])
    assert "transpose=1" in " ".join(cmd)


# --------------------------------------------------------------------------- #
# captions
# --------------------------------------------------------------------------- #
def test_build_command_includes_drawtext_and_font(tmp_path):
    img = tmp_path / "a.png"
    Image.new("RGB", (60, 40), (0, 0, 0)).save(img)
    cfg = _config()
    cmd = build_command(cfg, [MediaItem(path=img, duration=2, caption="Hello 'world'")])
    joined = " ".join(cmd)
    assert "drawtext=" in joined
    assert "fontfile=" in joined
    # single quote inside the caption must be escaped
    assert "'\\''" in joined


# --------------------------------------------------------------------------- #
# per-item durations (crossfade offset math)
# --------------------------------------------------------------------------- #
def test_crossfade_offsets_use_cumulative_durations():
    cfg = _config(transition="crossfade", crossfade_seconds=1.0)
    items = [
        MediaItem(path=Path("/in/a.jpg"), duration=2.0),
        MediaItem(path=Path("/in/b.jpg"), duration=3.0),
        MediaItem(path=Path("/in/c.jpg"), duration=4.0),
    ]
    cmd = build_command(cfg, items)
    joined = " ".join(cmd)
    # j=1: cum=2 - 1*1 = 1.000 ; j=2: cum=5 - 2*1 = 3.000
    assert "offset=1.000" in joined
    assert "offset=3.000" in joined
    assert joined.count("xfade=") == 2


# --------------------------------------------------------------------------- #
# audio track
# --------------------------------------------------------------------------- #
def test_build_command_includes_audio_chain_and_aac(tmp_path):
    img = tmp_path / "a.png"
    Image.new("RGB", (60, 40), (0, 0, 0)).save(img)
    audio = tmp_path / "music.wav"
    audio.write_bytes(b"RIFF")  # content irrelevant for command construction
    cfg = _config(audio_file=audio, audio_fade_in=2.0, audio_loop=True)
    cmd = build_command(cfg, [MediaItem(path=img, duration=2)])
    joined = " ".join(cmd)
    assert "[aout]" in joined
    assert "apad" in joined
    assert "atrim=0:" in joined
    assert "afade=t=in" in joined
    assert "aloop=" in joined
    assert "-c:a" in cmd and "aac" in cmd
    assert "-shortest" in cmd


def test_config_rejects_missing_audio_file():
    import pytest

    with pytest.raises(ConfigError):
        _config(audio_file=Path("/does/not/exist.wav")).validate()


def test_config_rejects_missing_font_and_bad_volume():
    import pytest

    with pytest.raises(ConfigError):
        _config(font_file=Path("/nope.ttf")).validate()
    with pytest.raises(ConfigError):
        _config(audio_volume=0).validate()


# --------------------------------------------------------------------------- #
# real cancellation terminates ffmpeg
# --------------------------------------------------------------------------- #
def test_render_cancel_raises_cancel_error(tmp_path):
    img = tmp_path / "a.png"
    Image.new("RGB", (320, 240), (0, 0, 0)).save(img)
    cfg = _config(
        input_dir=tmp_path,
        output_file=tmp_path / "out.mp4",
        delay_seconds=30.0,
        overwrite=True,
    )
    items = [MediaItem(path=img, duration=30.0)]

    def cancel_check():
        return True

    t0 = time.time()
    try:
        render(cfg, items, cancel_check=cancel_check)
        raise AssertionError("expected CancelError")
    except CancelError:
        assert time.time() - t0 < 5.0
