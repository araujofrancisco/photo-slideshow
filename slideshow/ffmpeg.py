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
from pathlib import Path

from .config import Config
from .errors import FFmpegNotFound, RenderError

# Frame rate used for the generated still frames. Kept constant so that the
# crossfade filter chain and any zoom/pan animation stay timebase-consistent.
FPS = 25

# Ken Burns: maximum zoom-in factor and per-frame increment. Gentle by design
# so it reads as a slow, cinematic drift rather than an aggressive push.
KB_MAX_ZOOM = 1.12
KB_ZOOM_RATE = 0.0006  # zoom added per output frame


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


def _per_input_filter(
    index: int,
    width: int,
    height: int,
    delay: float,
    ken_burns: bool,
    out_format: str = "yuv420p",
) -> str:
    """Scale-to-fit, pad with black bars, normalize pixel format + SAR.

    When `ken_burns` is enabled, a slow centered zoom/pan is composited first
    (via zoompan) so the motion happens within the source image before it is
    letterboxed into the target resolution. `out_format` is "yuv420p" for
    software encoders and "nv12,hwupload" for VAAPI (hardware frames).
    """
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
    return (
        f"[{index}:v]{zoom}scale=w={width}:h={height}:"
        f"force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,format={out_format}[v{index}]"
    )


def build_command(
    config: Config,
    images: list[Path],
    ffmpeg_exe: str = "ffmpeg",
    encoder: str | None = None,
) -> list[str]:
    """Construct the full ffmpeg command as a list of arguments.

    `ffmpeg_exe` defaults to "ffmpeg" (resolved via PATH at runtime) but can
    be injected, which keeps this function pure and testable. `encoder` is the
    *resolved* concrete encoder (callers pass the result of `resolve_encoder`),
    defaulting to `config.encoder`.
    """
    if not images:
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

    for i, image in enumerate(images):
        inputs += ["-loop", "1", "-t", str(config.delay_seconds), "-i", str(image)]
        filters.append(
            _per_input_filter(i, width, height, config.delay_seconds, config.ken_burns, out_format)
        )
        labels.append(f"[v{i}]")

    if config.transition == "crossfade" and len(images) > 1:
        chain = labels[0]
        for j in range(1, len(images)):
            offset = j * (config.delay_seconds - config.crossfade_seconds)
            out_label = f"[x{j}]" if j < len(images) - 1 else "[vout]"
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
        filters.append("".join(labels) + f"concat=n={len(images)}:v=1:a=0[vout]")
        map_label = "[vout]"

    command = [
        ffmpeg_exe,
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        map_label,
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


def _total_duration_seconds(config: Config, n_images: int) -> float:
    """Expected output duration in seconds for the given image count."""
    if config.transition == "crossfade":
        return n_images * config.delay_seconds - (n_images - 1) * config.crossfade_seconds
    return n_images * config.delay_seconds


def render(
    config: Config,
    images: list[Path],
    ffmpeg_exe: str | None = None,
    progress_callback: callable[[float], None] | None = None,
) -> list[str]:
    """Run ffmpeg to produce the video. Returns the command that was run.

    If `progress_callback` is provided, it is invoked with the encode progress
    as a float percentage (0-100) read from ffmpeg's `-progress` output. This
    is what lets the web UI show a live progress bar without blocking.
    """
    exe = ffmpeg_exe or find_ffmpeg()
    concrete = resolve_encoder(config.encoder, exe)
    command = build_command(config, images, exe, encoder=concrete)

    if progress_callback is None:
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RenderError(_format_error(command, proc.stderr, proc.returncode))
        return command

    # Progress mode: we ask ffmpeg to write machine-readable progress to a *file*
    # (not a pipe -- piping progress can deadlock when the encode runs inside a
    # background worker thread under an async event loop). The actual encode is
    # executed with subprocess.run in a *dedicated* thread, which is robust in
    # every threading context; this (calling) thread polls the progress file
    # until the encode thread finishes.
    total_ms = _total_duration_seconds(config, len(images)) * 1000.0

    progress_path = Path(tempfile.mkstemp(suffix=".progress", prefix="slideshow_")[1])
    stderr_path = Path(tempfile.mkstemp(suffix=".stderr", prefix="slideshow_")[1])

    command = command + ["-progress", str(progress_path), "-nostats"]

    # The encode itself runs in *this* (calling) thread via subprocess.run. We
    # deliberately avoid forking ffmpeg from a nested thread, which can deadlock
    # under CPython's fork-when-other-threads-hold-locks behavior. A separate,
    # lightweight thread only polls the progress file (no subprocess) so it can
    # stream updates to the callback without blocking this thread.
    done = threading.Event()

    def _poll_progress() -> None:
        while not done.wait(0.2):
            pct = _read_progress_file(progress_path, total_ms)
            if pct is not None:
                try:
                    progress_callback(pct)
                except Exception:
                    pass

    poll_thread = threading.Thread(target=_poll_progress, daemon=True)
    poll_thread.start()

    try:
        with open(stderr_path, "w", encoding="utf-8", errors="replace") as err_fh:
            returncode = subprocess.run(
                command, stdout=subprocess.DEVNULL, stderr=err_fh
            ).returncode
    finally:
        done.set()
        poll_thread.join()
        try:
            progress_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Capture any final progress written in the tail of the encode.
    pct = _read_progress_file(progress_path, total_ms)
    if pct is not None:
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

    if returncode != 0:
        raise RenderError(_format_error(command, stderr_text, returncode))
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
