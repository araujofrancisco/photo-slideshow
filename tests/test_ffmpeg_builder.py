"""Unit tests for ffmpeg command construction (no ffmpeg execution)."""

from pathlib import Path

from slideshow.config import Config
from slideshow.ffmpeg import KB_MAX_ZOOM, KB_ZOOM_RATE, build_command


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


def test_hard_cut_command():
    cfg = _config()
    images = [Path(f"/in/img{i}.jpg") for i in range(3)]
    cmd = build_command(cfg, images)
    joined = " ".join(cmd)
    # one -loop input per image
    assert cmd.count("-loop") == 3
    # concat filter with n=3
    assert "concat=n=3:v=1:a=0[vout]" in joined
    # each input is scaled/padded/normalized
    assert joined.count("force_original_aspect_ratio=decrease") == 3
    assert "-c:v" in cmd and "libx264" in cmd
    assert "+faststart" in cmd


def test_crossfade_offsets_and_chain():
    cfg = _config(transition="crossfade", delay_seconds=5.0, crossfade_seconds=1.0)
    images = [Path(f"/in/img{i}.jpg") for i in range(3)]
    cmd = build_command(cfg, images)
    joined = " ".join(cmd)
    # offsets: 1*(5-1)=4.000 and 2*(5-1)=8.000
    assert "offset=4.000" in joined
    assert "offset=8.000" in joined
    # two xfade filters for three images
    assert joined.count("xfade=") == 2
    assert "[vout]" in joined


def test_single_image_crossfade_maps_directly():
    cfg = _config(transition="crossfade")
    cmd = build_command(cfg, [Path("/in/only.jpg")])
    joined = " ".join(cmd)
    assert "xfade=" not in joined
    assert "-map" in cmd
    assert cmd[cmd.index("-map") + 1] == "[v0]"


def test_overwrite_flag():
    cfg = _config(overwrite=True)
    cmd = build_command(cfg, [Path("/in/a.jpg")])
    assert "-y" in cmd
    cfg2 = _config(overwrite=False)
    cmd2 = build_command(cfg2, [Path("/in/a.jpg")])
    assert "-n" in cmd2


def test_ken_burns_injects_zoompan():
    cfg = _config(ken_burns=True, delay_seconds=4.0)
    cmd = build_command(cfg, [Path("/in/a.jpg")])
    joined = " ".join(cmd)
    assert "zoompan=" in joined
    # d=1 keeps the zoom continuous across the looped still stream
    assert "d=1" in joined
    # zoom expression bounds the zoom factor
    assert f"min(1.0+{KB_ZOOM_RATE}*on,{KB_MAX_ZOOM})" in joined
    # letterbox scaling still applied after the zoom
    assert "force_original_aspect_ratio=decrease" in joined


def test_ken_burns_off_by_default_has_no_zoompan():
    cfg = _config()
    cmd = build_command(cfg, [Path("/in/a.jpg")])
    assert "zoompan=" not in " ".join(cmd)


def test_encoder_is_used_and_overridable():
    cfg = _config()
    assert "-c:v" in build_command(cfg, [Path("/in/a.jpg")])
    assert "libx264" in build_command(cfg, [Path("/in/a.jpg")])
    cfg_gpu = _config(encoder="h264_nvenc")
    assert "h264_nvenc" in build_command(cfg_gpu, [Path("/in/a.jpg")])


def test_is_encoder_available():
    from slideshow.ffmpeg import is_encoder_available

    assert is_encoder_available("libx264", "ffmpeg") is True
    assert is_encoder_available("definitely_not_an_encoder_xyz", "ffmpeg") is False


def test_resolve_encoder_auto_picks_valid():
    from slideshow.ffmpeg import resolve_encoder

    chosen = resolve_encoder("auto", "ffmpeg")
    assert chosen in ("h264_nvenc", "h264_qsv", "h264_videotoolbox", "libx264")


def test_resolve_encoder_explicit_missing_raises():
    import pytest

    from slideshow.errors import RenderError
    from slideshow.ffmpeg import resolve_encoder

    with pytest.raises(RenderError):
        resolve_encoder("definitely_not_an_encoder_xyz", "ffmpeg")


def test_resolve_encoder_explicit_valid_returned():
    from slideshow.ffmpeg import resolve_encoder

    assert resolve_encoder("libx264", "ffmpeg") == "libx264"
