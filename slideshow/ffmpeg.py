"""FFmpeg command construction and execution.

This module is intentionally free of business logic beyond "how do we talk
to ffmpeg". Building the command and running it are separate functions so
the builder can be unit-tested without invoking ffmpeg, and so callers can
inspect/echo the command (e.g. --dry-run).

Rendering strategy
------------------
Each image becomes its own input:  `-loop 1 -t <delay> -i <file>`.
This gives every image an exact, identical duration and lets us normalize
size/format per input before compositing.

* Hard cut  -> the `concat` filter stitches the (scaled/padded) clips.
* Crossfade -> a chain of `xfade` filters. With N images of duration D and
  crossfade F, the j-th transition (1-indexed) starts at offset
  `j * (D - F)` in the running timeline. This yields each image a net
  on-screen time of D with an F-second overlap, and a final duration of
  `N*D - (N-1)*F`.

All inputs are scaled to fit (preserving aspect ratio) inside the target
resolution and padded with black bars, so mixed sizes/orientations compose
cleanly and the output is uniformly sized.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

from .config import Config
from .errors import CancelError, FFmpegNotFound, RenderError
from .scanner import MediaItem

# Frame rate used for the generated still frames. Kept constant so that the
# crossfade filter chain and any zoom/pan animation stay timebase-consistent.
FPS = 25

# Ken Burns: maximum zoom-in factor and per-frame increment. Gentle by design
# so it reads as a slow, cinematic drift rather than an aggressive push.
KB_MAX_ZOOM = 1.12
KB_ZOOM_RATE = 0.0006  # zoom added per output frame

# EXIF orientation tag id.
_EXIF_ORIENTATION_TAG = 0x0112

# Map EXIF orientation (1-8) to the ffmpeg filter chain that makes it upright.
# 1 is already correct; 2-4 are simple flips; 6/8 are the common phone rotations.
_ORIENTATION_FILTER = {
    1: None,
    2: "hflip",
    3: "transpose=1,transpose=1",
    4: "vflip",
    5: "hflip,transpose=2",
    6: "transpose=1",
    7: "hflip,transpose=1",
    8: "transpose=2",
}


def find_ffmpeg() -> str:
    """Return the ffmpeg executable path or raise FFmpegNotFound."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise FFmpegNotFound(
            "ffmpeg not found on PATH. Install FFmpeg (e.g. `apt install ffmpeg` "
            "on Debian/Ubuntu, `brew install ffmpeg` on macOS) and retry."
        )
    return exe


def is_encoder_available(encoder: str, ffmpeg_exe: str) -> bool:
    """Return True if ffmpeg can use the requested video encoder."""
    proc = subprocess.run(
        [ffmpeg_exe, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
    )
    pattern = re.compile(r"^\s*\S+\s+" + re.escape(encoder) + r"\b")
    return any(pattern.match(line) for line in proc.stdout.splitlines())


# Priority order for automatic selection. NVIDIA is typically fastest, then
# Intel QuickSync, then macOS VideoToolbox. VAAPI (Linux/AMD/Intel) is excluded
# from auto because it requires an explicit device + hardware-upload filter
# chain; users can still opt in explicitly via --encoder h264_vaapi.
AUTO_CANDIDATES = ["h264_nvenc", "h264_qsv", "h264_videotoolbox"]

# Optimal, balanced presets per hardware encoder (quality vs. speed).
ENCODER_PRESETS = {
    "h264_nvenc": "p4",  # NVENC: p1 (fast) .. p7 (slow); p4 is a solid default
    "h264_qsv": "medium",  # QuickSync
    "h264_videotoolbox": None,  # VideoToolbox has no preset; uses sane defaults
}


def _find_vaapi_device() -> str | None:
    import glob

    for dev in ("/dev/dri/renderD128", "/dev/dri/renderD129"):
        if Path(dev).exists():
            return dev
    matches = glob.glob("/dev/dri/renderD*")
    return matches[0] if matches else None


def _probe_encoder(encoder: str, ffmpeg_exe: str) -> bool:
    """Functionally probe an encoder with a 0.2s black-frame encode.

    Listing availability is not enough (e.g. nvenc may be compiled in but no
    GPU present). A real micro-encode confirms the encoder actually works.
    """
    if encoder == "h264_vaapi":
        device = _find_vaapi_device()
        if not device:
            return False
        cmd = [
            ffmpeg_exe,
            "-vaapi_device",
            device,
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240",
            "-t",
            "0.2",
            "-vf",
            "format=nv12,hwupload",
            "-c:v",
            "h264_vaapi",
            "-f",
            "null",
            "-",
        ]
    else:
        cmd = [
            ffmpeg_exe,
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240",
            "-t",
            "0.2",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            encoder,
            "-f",
            "null",
            "-",
        ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0


def resolve_encoder(encoder: str, ffmpeg_exe: str) -> str:
    """Resolve the concrete encoder to use.

    * An explicit encoder is validated for availability and returned.
    * "auto" probes AUTO_CANDIDATES (in priority order) with a functional
      test and returns the first that works; otherwise falls back to libx264.
    """
    if encoder and encoder != "auto":
        if not is_encoder_available(encoder, ffmpeg_exe):
            raise RenderError(
                f"video encoder '{encoder}' is not available in this FFmpeg build. "
                f"Use 'libx264' or check `ffmpeg -encoders` for hardware options "
                f"(e.g. h264_nvenc, h264_qsv, h264_vaapi, h264_videotoolbox)."
            )
        return encoder

    for candidate in AUTO_CANDIDATES:
        if is_encoder_available(candidate, ffmpeg_exe) and _probe_encoder(candidate, ffmpeg_exe):
            return candidate
    return "libx264"


def _exif_orientation(path: Path) -> int:
    """Return the EXIF orientation tag (1-8) for *path*, or 1 if absent/invalid."""
    try:
        with Image.open(path) as img:
            value = img.getexif().get(_EXIF_ORIENTATION_TAG)
            if isinstance(value, int) and 1 <= value <= 8:
                return value
    except Exception:
        # Unreadable / corrupt metadata shouldn't block the render.
        pass
    return 1


def _transpose_filter(path: Path, autorotate: bool) -> str | None:
    """Return an ffmpeg filter prefix that uprights *path* per EXIF, or None."""
    if not autorotate:
        return None
    orientation = _exif_orientation(path)
    return _ORIENTATION_FILTER.get(orientation)


def _escape_drawtext(text: str) -> str:
    """Escape a caption so it is safe inside an ffmpeg drawtext ``text='...'``.

    Inside a single-quoted filter value, the only character that needs
    escaping is the single quote (written ``'\''``); ``%`` enables drawtext's
    strftime expansion and must be escaped so literal percent signs render.
    """
    text = text.replace("%", "\\%")
    text = text.replace("'", "'\\''")
    return text


def _per_input_filter(
    index: int,
    width: int,
    height: int,
    ken_burns: bool,
    out_format: str = "yuv420p",
    caption: str | None = None,
    transpose: str | None = None,
    font_file: Path | None = None,
) -> str:
    """Scale-to-fit, pad with black bars, normalize pixel format + SAR.

    When `ken_burns` is enabled, a slow centered zoom/pan is composited first
    (via zoompan) so the motion happens within the source image before it is
    letterboxed into the target resolution. `out_format` is "yuv420p" for
    software encoders and "nv12,hwupload" for VAAPI (hardware frames).

    `transpose` (an EXIF-derived filter prefix) uprights rotated phone photos,
    and `caption` burns a bottom-centered text overlay onto the slide.
    """
    prefix = ""
    if transpose:
        prefix = f"{transpose},"

    zoom = ""
    if ken_burns:
        # The input is a 1-frame-per-D-second still stream, so d=1 makes each
        # still frame advance the global output counter `on` by one, producing
        # a smooth continuous zoom. It keeps the source resolution while
        # zooming, before the letterbox scale/pad step.
        zoom = (
            f"zoompan=z='min(1.0+{KB_ZOOM_RATE}*on,{KB_MAX_ZOOM})':"
            f"d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"fps={FPS},"
        )

    caption_filter = ""
    if caption:
        safe = _escape_drawtext(caption)
        fontsize = max(18, height // 20)
        fontfile = f"fontfile='{Path(font_file).as_posix()}':" if font_file else ""
        caption_filter = (
            f",drawtext={fontfile}text='{safe}':fontcolor=white:"
            f"fontsize={fontsize}:box=1:boxcolor=black@0.4:boxborderw=12:"
            f"x=(w-text_w)/2:y=h-th-48"
        )

    return (
        f"[{index}:v]{prefix}{zoom}scale=w={width}:h={height}:"
        f"force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1{caption_filter},format={out_format}[v{index}]"
    )


def _normalize_items(items: list[Path | MediaItem], default_duration: float) -> list[MediaItem]:
    """Coerce a mix of ``Path``/``MediaItem`` inputs into uniform ``MediaItem``s."""
    norm: list[MediaItem] = []
    for it in items:
        if isinstance(it, MediaItem):
            norm.append(it)
        else:
            norm.append(MediaItem(path=Path(it), duration=default_duration))
    return norm


def build_command(
    config: Config,
    items: list[Path | MediaItem],
    ffmpeg_exe: str = "ffmpeg",
    encoder: str | None = None,
) -> list[str]:
    """Construct the full ffmpeg command as a list of arguments.

    `items` is a list of image paths or :class:`~slideshow.scanner.MediaItem`
    objects (the latter allowing per-image duration/caption overrides).
    `ffmpeg_exe` defaults to "ffmpeg" (resolved via PATH at runtime) but can
    be injected, which keeps this function pure and testable. `encoder` is the
    *resolved* concrete encoder (callers pass the result of `resolve_encoder`),
    defaulting to `config.encoder`.
    """
    items = _normalize_items(items, config.delay_seconds)
    if not items:
        raise RenderError("no images supplied to build_command.")

    enc = encoder or config.encoder
    is_vaapi = enc == "h264_vaapi"
    out_format = "nv12,hwupload" if is_vaapi else "yuv420p"
    preset = ENCODER_PRESETS.get(enc)

    width, height = config.width, config.height
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []

    if is_vaapi:
        device = _find_vaapi_device()
        if not device:
            raise RenderError("VAAPI requested but no /dev/dri/renderD* device found.")
        inputs += ["-vaapi_device", device]

    for i, item in enumerate(items):
        inputs += ["-loop", "1", "-t", str(item.duration), "-i", str(item.path)]
        transpose = _transpose_filter(item.path, config.autorotate)
        filters.append(
            _per_input_filter(
                i,
                width,
                height,
                config.ken_burns,
                out_format,
                caption=item.caption,
                transpose=transpose,
                font_file=config.font_file,
            )
        )
        labels.append(f"[v{i}]")

    if config.transition == "crossfade" and len(items) > 1:
        chain = labels[0]
        cumulative = 0.0
        for j in range(1, len(items)):
            cumulative += items[j - 1].duration
            # Offset of the j-th transition in the running timeline. Each prior
            # crossfade already shortened the timeline by crossfade_seconds, so
            # we subtract j * F from the cumulative on-screen time.
            offset = cumulative - j * config.crossfade_seconds
            out_label = f"[x{j}]" if j < len(items) - 1 else "[vout]"
            filters.append(
                f"{chain}{labels[j]}xfade=transition=fade:"
                f"duration={config.crossfade_seconds}:offset={offset:.3f}{out_label}"
            )
            chain = out_label
        map_label = "[vout]"
    elif config.transition == "crossfade":
        # Single image: no transition possible, just map the normalized clip.
        map_label = "[v0]"
    else:
        filters.append("".join(labels) + f"concat=n={len(items)}:v=1:a=0[vout]")
        map_label = "[vout]"

    # Optional background audio: fit it to the video length, apply fades, and
    # map it as a second output stream.
    if config.audio_file is not None:
        audio_index = len(items)
        inputs += ["-i", str(config.audio_file)]
        total = _total_duration_seconds(config, items)
        chain_parts: list[str] = []
        if config.audio_normalize:
            chain_parts.append("loudnorm")
        if config.audio_loop:
            chain_parts.append("aloop=loop=-1:size=2147483647")
        chain_parts.append("apad")
        chain_parts.append(f"atrim=0:{total:.3f}")
        chain_parts.append(f"afade=t=in:st=0:d={config.audio_fade_in:.3f}")
        fade_out_start = max(0.0, total - config.audio_fade_out)
        chain_parts.append(f"afade=t=out:st={fade_out_start:.3f}:d={config.audio_fade_out:.3f}")
        if config.audio_volume != 1.0:
            chain_parts.append(f"volume={config.audio_volume:.3f}")
        filters.append(f"[{audio_index}:a]{','.join(chain_parts)}[aout]")

    command = [
        ffmpeg_exe,
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        map_label,
    ]
    if config.audio_file is not None:
        command += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    command += [
        "-c:v",
        enc,
        "-pix_fmt",
        "yuv420p",  # required for broad player/QuickTime compatibility
        *(["-preset", preset] if preset else []),
        *(_quality_args(config, enc)),
        "-movflags",
        "+faststart",
        "-y" if config.overwrite else "-n",
        str(config.output_file),
    ]
    return command


def _quality_args(config: Config, encoder: str) -> list[str]:
    """Build bitrate/CRF flags for the selected encoder.

    Hardware encoders (nvenc, qsv, videotoolbox) use -b:v for bitrate.
    Software encoder (libx264) uses -crf for constant-rate-factor quality.
    If bitrate is set to "auto", we let the encoder pick its default.
    """
    args: list[str] = []
    bitrate = getattr(config, "bitrate", "auto")
    crf = getattr(config, "crf", 23)

    if bitrate and bitrate != "auto":
        args.extend(["-b:v", bitrate])
    elif encoder == "libx264":
        # libx264 uses CRF by default; only pass -crf if it differs from the default (23)
        if crf != 23:
            args.extend(["-crf", str(crf)])
    else:
        # Hardware encoders: use -qp as a fallback quality knob if CRF is non-default
        if crf != 23:
            args.extend(["-qp", str(crf)])
    return args


def _total_duration_seconds(config: Config, items: list[Path | MediaItem]) -> float:
    """Expected output duration in seconds for the given image items."""
    durations = [it.duration if isinstance(it, MediaItem) else config.delay_seconds for it in items]
    if config.transition == "crossfade" and len(durations) > 1:
        return sum(durations) - (len(durations) - 1) * config.crossfade_seconds
    return sum(durations)


def render(
    config: Config,
    items: list[Path | MediaItem],
    ffmpeg_exe: str | None = None,
    progress_callback: callable[[float], None] | None = None,
    cancel_check: callable[[], bool] | None = None,
) -> list[str]:
    """Run ffmpeg to produce the video. Returns the command that was run.

    If `progress_callback` is provided, it is invoked with the encode progress
    as a float percentage (0-100) read from ffmpeg's `-progress` output. This
    is what lets the web UI show a live progress bar without blocking.

    If `cancel_check` is provided, it is polled while encoding; when it returns
    True the ffmpeg process is terminated and :class:`CancelError` is raised.
    """
    exe = ffmpeg_exe or find_ffmpeg()
    concrete = resolve_encoder(config.encoder, exe)
    command = build_command(config, items, exe, encoder=concrete)

    if progress_callback is None and cancel_check is None:
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RenderError(_format_error(command, proc.stderr, proc.returncode))
        return command

    # Progress/cancel mode: we ask ffmpeg to write machine-readable progress to
    # a *file* (not a pipe -- piping progress can deadlock when the encode runs
    # inside a background worker thread under an async event loop). A lightweight
    # poll thread reads that file and streams updates to the callback without
    # blocking the calling thread, which runs the encode and watches for cancel.
    total_ms = _total_duration_seconds(config, items) * 1000.0

    progress_path = Path(tempfile.mkstemp(suffix=".progress", prefix="slideshow_")[1])
    stderr_path = Path(tempfile.mkstemp(suffix=".stderr", prefix="slideshow_")[1])

    command = command + ["-progress", str(progress_path), "-nostats"]

    done = threading.Event()

    def _poll_progress() -> None:
        while not done.wait(0.2):
            pct = _read_progress_file(progress_path, total_ms)
            if pct is not None and progress_callback is not None:
                try:
                    progress_callback(pct)
                except Exception:
                    pass

    poll_thread = threading.Thread(target=_poll_progress, daemon=True)
    poll_thread.start()

    cancelled = False
    returncode = 0
    try:
        with open(stderr_path, "w", encoding="utf-8", errors="replace") as err_fh:
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=err_fh)
            while proc.poll() is None:
                if cancel_check is not None and cancel_check():
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    cancelled = True
                    break
                time.sleep(0.1)
            if not cancelled:
                returncode = proc.wait()
    finally:
        done.set()
        poll_thread.join()
        try:
            progress_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Capture any final progress written in the tail of the encode.
    pct = _read_progress_file(progress_path, total_ms)
    if pct is not None and progress_callback is not None:
        try:
            progress_callback(pct)
        except Exception:
            pass

    stderr_text = ""
    try:
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    try:
        stderr_path.unlink(missing_ok=True)
    except OSError:
        pass

    if cancelled:
        raise CancelError("Render cancelled by user.")

    if returncode != 0:
        raise RenderError(_format_error(command, stderr_text, returncode))
    if progress_callback is not None:
        try:
            progress_callback(100.0)
        except Exception:
            pass
    return command


def _read_progress_file(path: Path, total_ms: float) -> float | None:
    """Parse ffmpeg's `-progress` file and return a 0-100 percentage, or None.

    ffmpeg appends `out_time_ms=<milliseconds>` lines as it encodes; we take the
    latest one seen and scale it against the expected total duration.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    current_ms = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("out_time_ms="):
            value = line.split("=", 1)[1].strip()
            if value.isdigit():
                current_ms = float(value)
    if current_ms is None or total_ms <= 0:
        return None
    return max(0.0, min(100.0, current_ms / total_ms * 100.0))


def _format_error(command: list[str], stderr: str, returncode: int) -> str:
    tail = stderr.strip().splitlines()[-25:]
    return (
        f"ffmpeg exited with code {returncode}.\n"
        f"Command: {' '.join(command)}\n\n"
        f"{chr(10).join(tail)}"
    )
