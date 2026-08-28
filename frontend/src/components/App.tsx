import { useState, useEffect, useRef, useCallback } from "react";
import { ToastProvider, useToast } from "./Toast";
import { ConfirmModal, VideoPreviewModal, ImagePreviewModal } from "./Modal";
import DropZone from "./DropZone";
import FileList, { type SlideItem } from "./FileList";
import { relativeTime, formatDuration } from "../lib/time";
import { RESOLUTION_PRESETS, CUSTOM_PRESET_VALUE } from "../lib/presets";
import "./styles.css";

type JobStatus = "queued" | "processing" | "done" | "error" | "cancelled";

interface Job {
  id: string;
  status: JobStatus;
  progress: number;
  options: {
    delay?: number;
    transition?: string;
    crossfade?: number;
    resolution?: string;
    ken_burns?: boolean;
    autorotate?: boolean;
    encoder?: string;
    bitrate?: string;
    crf?: number;
  };
  error?: string | null;
  download_url?: string | null;
  created_at: number;
}

interface Props {
  encoderChoices: [string, string][];
}

const QUALITY_PRESETS = [
  { label: "Balanced", bitrate: "auto", crf: 23 },
  { label: "High Quality", bitrate: "auto", crf: 18 },
  { label: "Small File", bitrate: "auto", crf: 28 },
  { label: "Custom", bitrate: "custom", crf: -1 },
];

function AppInner({ encoderChoices }: Props) {
  const { toast } = useToast();
  const [slides, setSlides] = useState<SlideItem[]>([]);
  const [delay, setDelay] = useState(5);
  const [transition, setTransition] = useState("cut");
  const [crossfade, setCrossfade] = useState(1);
  const [resolution, setResolution] = useState("1920x1080");
  const [resolutionPreset, setResolutionPreset] = useState("1920x1080");
  const [kenBurns, setKenBurns] = useState(false);
  const [autorotate, setAutorotate] = useState(false);
  const [encoder, setEncoder] = useState("auto");

  // Background audio
  const [audio, setAudio] = useState<File | null>(null);
  const [audioFadeIn, setAudioFadeIn] = useState(1);
  const [audioFadeOut, setAudioFadeOut] = useState(1);
  const [audioLoop, setAudioLoop] = useState(false);
  const [audioNormalize, setAudioNormalize] = useState(false);

  // Quality options
  const [qualityPreset, setQualityPreset] = useState("auto");
  const [customBitrate, setCustomBitrate] = useState("8M");
  const [customCrf, setCustomCrf] = useState(23);

  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [progress, setProgress] = useState(0);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const eventSourceRef = useRef<EventSource | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<Job | null>(null);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<{ src: string; alt: string } | null>(null);

  // Keyboard navigation state
  const [focusedFileIndex, setFocusedFileIndex] = useState<number | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const res = await fetch("/api/jobs");
      if (res.ok) setJobs(await res.json());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadJobs();
    return () => {
      eventSourceRef.current?.close();
    };
  }, [loadJobs]);

  const subscribeToJob = useCallback((id: string) => {
    eventSourceRef.current?.close();
    const es = new EventSource(`/api/jobs/${id}/stream`);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const j: Job = JSON.parse(event.data);
        if (j.error) {
          toast("error", j.error);
          es.close();
          setStatus(j.status);
          loadJobs();
          return;
        }
        setStatus(j.status);
        setProgress(j.progress);
        if (j.status === "done" || j.status === "error" || j.status === "cancelled") {
          es.close();
          if (j.status === "done") toast("success", "Render complete!");
          else if (j.status === "error") toast("error", j.error || "Render failed");
          else toast("info", "Render cancelled");
          loadJobs();
        }
      } catch {
        /* parse error, ignore */
      }
    };

    es.onerror = () => {
      es.close();
      // Fall back to polling if SSE fails
      const poll = setInterval(async () => {
        try {
          const res = await fetch(`/api/jobs/${id}`);
          if (!res.ok) return;
          const j: Job = await res.json();
          setStatus(j.status);
          setProgress(j.progress);
          if (j.status === "done" || j.status === "error" || j.status === "cancelled") {
            clearInterval(poll);
            if (j.status === "done") toast("success", "Render complete!");
            else if (j.status === "error") toast("error", j.error || "Render failed");
            else toast("info", "Render cancelled");
            loadJobs();
          }
        } catch {
          /* ignore */
        }
      }, 1500);
    };
  }, [toast, loadJobs]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Delete" && focusedFileIndex !== null && slides.length > 0) {
        e.preventDefault();
        setSlides(slides.filter((_, idx) => idx !== focusedFileIndex));
        setFocusedFileIndex(null);
      }
      if (e.key === "Escape") {
        setImagePreview(null);
        setPreviewSrc(null);
        setDeleteTarget(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [slides, focusedFileIndex]);

  const MAX_FILES = 200;
  const MAX_FILE_MB = 20;

  const addSlides = (newFiles: File[]) => {
    setSlides((prev) => [
      ...prev,
      ...newFiles.map((f) => ({ file: f, duration: delay, caption: "" })),
    ]);
  };

  const onMetaChange = (index: number, patch: Partial<SlideItem>) => {
    setSlides((prev) =>
      prev.map((s, i) => (i === index ? { ...s, ...patch } : s)),
    );
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (slides.length === 0) {
      toast("error", "Please add at least one image.");
      return;
    }
    if (slides.length > MAX_FILES) {
      toast("error", `Too many files (max ${MAX_FILES}).`);
      return;
    }
    const oversized = slides.find((s) => s.file.size > MAX_FILE_MB * 1024 * 1024);
    if (oversized) {
      toast("error", `File too large: ${oversized.file.name} (max ${MAX_FILE_MB}MB).`);
      return;
    }
    setSubmitting(true);
    const fd = new FormData();
    slides.forEach((s) => fd.append("files", s.file));

    // Only send a per-image manifest when any slide diverges from the global
    // defaults (custom duration or a caption). Otherwise the server falls back
    // to natural ordering with the global delay.
    const useItems = slides.some(
      (s) => s.caption.trim() !== "" || s.duration !== delay,
    );
    if (useItems) {
      const items = slides.map((s, idx) => ({
        name: `${String(idx).padStart(4, "0")}_${s.file.name}`,
        duration: s.duration,
        caption: s.caption.trim() || null,
      }));
      fd.append("items", JSON.stringify(items));
    }

    fd.append("delay", String(delay));
    fd.append("transition", transition);
    fd.append("crossfade", String(crossfade));
    fd.append("resolution", resolution);
    fd.append("ken_burns", String(kenBurns));
    fd.append("no_autorotate", String(!autorotate));
    fd.append("encoder", encoder);
    if (audio) {
      fd.append("audio", audio);
      fd.append("audio_fade_in", String(audioFadeIn));
      fd.append("audio_fade_out", String(audioFadeOut));
      fd.append("audio_loop", String(audioLoop));
      fd.append("audio_normalize", String(audioNormalize));
    }

    // Quality options
    const q = QUALITY_PRESETS.find((p) => p.label === qualityPreset);
    if (q && q.bitrate !== "custom") {
      fd.append("bitrate", q.bitrate);
      fd.append("crf", String(q.crf));
    } else if (q && q.bitrate === "custom") {
      fd.append("bitrate", customBitrate);
      fd.append("crf", String(customCrf));
    }

    try {
      const res = await fetch("/api/render", { method: "POST", body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast("error", err.detail || "Failed to start render.");
        return;
      }
      const data = await res.json();
      setStatus("processing");
      setProgress(0);
      setCurrentJobId(data.job_id);
      subscribeToJob(data.job_id);
    } catch {
      toast("error", "Network error — is the server running?");
    } finally {
      setSubmitting(false);
    }
  };

  const onDelete = async (job: Job) => {
    await fetch(`/api/jobs/${job.id}`, { method: "DELETE" });
    toast("info", "Job deleted");
    loadJobs();
  };

  const onCancel = async (jobId: string) => {
    eventSourceRef.current?.close();
    await fetch(`/api/jobs/${jobId}/cancel`, { method: "DELETE" });
    toast("info", "Render cancelled");
    setStatus(null);
    loadJobs();
  };

  const handlePresetChange = (val: string) => {
    setResolutionPreset(val);
    if (val !== CUSTOM_PRESET_VALUE) {
      setResolution(val);
    }
  };

  const estimatedTime =
    slides.length > 0
      ? Math.max(
          5,
          (slides.reduce((a, s) => a + s.duration, 0) -
            (transition === "crossfade"
              ? (slides.length - 1) * crossfade
              : 0)) *
            0.3,
        )
      : null;

  return (
    <div className="app" role="main" aria-label="Photo Slideshow Maker">
      <form className="card" onSubmit={onSubmit} aria-label="Create slideshow">
        <DropZone files={slides.map((s) => s.file)} onFiles={addSlides} />
        <FileList
          slides={slides}
          defaultDuration={delay}
          onReorder={setSlides}
          onRemove={(i) => setSlides(slides.filter((_, idx) => idx !== i))}
          onMetaChange={onMetaChange}
          onImageClick={(src, alt) => setImagePreview({ src, alt })}
          focusedIndex={focusedFileIndex}
          onFocus={setFocusedFileIndex}
        />

        <div className="row">
          <label className="field">
            <span>Delay (s)</span>
            <input
              type="number"
              min={0.1}
              step={0.1}
              value={delay}
              onChange={(e) => setDelay(Number(e.target.value))}
              aria-label="Seconds each image is shown"
            />
          </label>
          <label className="field">
            <span>Transition</span>
            <select value={transition} onChange={(e) => setTransition(e.target.value)} aria-label="Transition type">
              <option value="cut">Cut</option>
              <option value="crossfade">Crossfade</option>
            </select>
          </label>
          {transition === "crossfade" && (
            <label className="field">
              <span>Crossfade (s)</span>
              <input
                type="number"
                min={0}
                step={0.1}
                value={crossfade}
                onChange={(e) => setCrossfade(Number(e.target.value))}
                aria-label="Crossfade duration in seconds"
              />
            </label>
          )}
        </div>

        <div className="row">
          <label className="field">
            <span>Resolution</span>
            <select value={resolutionPreset} onChange={(e) => handlePresetChange(e.target.value)} aria-label="Resolution preset">
              {RESOLUTION_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
              <option value={CUSTOM_PRESET_VALUE}>Custom</option>
            </select>
          </label>
          {resolutionPreset === CUSTOM_PRESET_VALUE && (
            <label className="field">
              <span>Custom Resolution</span>
              <input
                type="text"
                value={resolution}
                onChange={(e) => setResolution(e.target.value)}
                placeholder="1920x1080"
                aria-label="Custom resolution in WIDTHxHEIGHT format"
              />
            </label>
          )}
          <label className="field">
            <span>Encoder</span>
            <select value={encoder} onChange={(e) => setEncoder(e.target.value)} aria-label="Video encoder">
              {encoderChoices.map(([val, label]) => (
                <option key={val} value={val}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field checkbox">
            <input
              type="checkbox"
              checked={kenBurns}
              onChange={(e) => setKenBurns(e.target.checked)}
              aria-label="Enable Ken Burns effect"
            />
            <span>Ken Burns</span>
          </label>
          <label className="field checkbox">
            <input
              type="checkbox"
              checked={autorotate}
              onChange={(e) => setAutorotate(e.target.checked)}
              aria-label="Auto-rotate images using EXIF orientation"
            />
            <span>Auto-rotate (EXIF)</span>
          </label>
        </div>

        {/* Quality options */}
        <div className="row">
          <label className="field">
            <span>Quality</span>
            <select
              value={qualityPreset}
              onChange={(e) => setQualityPreset(e.target.value)}
              aria-label="Video quality preset"
            >
              {QUALITY_PRESETS.map((p) => (
                <option key={p.label} value={p.label}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          {qualityPreset === "Custom" && (
            <>
              <label className="field">
                <span>Bitrate</span>
                <input
                  type="text"
                  value={customBitrate}
                  onChange={(e) => setCustomBitrate(e.target.value)}
                  placeholder="8M"
                  aria-label="Video bitrate"
                />
              </label>
              <label className="field">
                <span>CRF (0-51)</span>
                <input
                  type="number"
                  min={0}
                  max={51}
                  value={customCrf}
                  onChange={(e) => setCustomCrf(Number(e.target.value))}
                  aria-label="Constant Rate Factor"
                />
              </label>
            </>
          )}
        </div>

        {/* Audio track */}
        <div className="row audio-row">
          <label className="field">
            <span>Background audio</span>
            <input
              type="file"
              accept="audio/*"
              onChange={(e) => setAudio(e.target.files?.[0] ?? null)}
              aria-label="Background audio file"
            />
          </label>
          {audio && (
            <>
              <label className="field">
                <span>Fade in (s)</span>
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  value={audioFadeIn}
                  onChange={(e) => setAudioFadeIn(Number(e.target.value))}
                  aria-label="Audio fade in seconds"
                />
              </label>
              <label className="field">
                <span>Fade out (s)</span>
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  value={audioFadeOut}
                  onChange={(e) => setAudioFadeOut(Number(e.target.value))}
                  aria-label="Audio fade out seconds"
                />
              </label>
              <label className="field checkbox">
                <input
                  type="checkbox"
                  checked={audioLoop}
                  onChange={(e) => setAudioLoop(e.target.checked)}
                  aria-label="Loop audio to fill video"
                />
                <span>Loop</span>
              </label>
              <label className="field checkbox">
                <input
                  type="checkbox"
                  checked={audioNormalize}
                  onChange={(e) => setAudioNormalize(e.target.checked)}
                  aria-label="Normalize audio loudness"
                />
                <span>Normalize</span>
              </label>
            </>
          )}
        </div>

        {estimatedTime !== null && (
          <p className="estimate" aria-live="polite">
            Estimated render: ~{formatDuration(estimatedTime)}
          </p>
        )}

        <button type="submit" className="primary" disabled={submitting || slides.length === 0} aria-label="Create slideshow">
          {submitting ? (
            <>
              <span className="spinner" aria-hidden="true" /> Uploading…
            </>
          ) : (
            "Create slideshow"
          )}
        </button>
      </form>

      {status && (
        <div className="card status" role="status" aria-live="polite" aria-label="Render progress">
          <div className="progress-wrap">
            <div className="progress-label">
              <span>
                Status: {status}
                {status === "processing" && currentJobId && (
                  <button className="btn-cancel" onClick={() => onCancel(currentJobId)} aria-label="Cancel render">
                    Cancel
                  </button>
                )}
              </span>
              <span>{Math.round(progress)}%</span>
            </div>
            <div className="progress-bar" role="progressbar" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}>
              <div
                className="progress-fill"
                style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
              />
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <h2>History</h2>
        {jobs.length === 0 && (
          <div className="empty-state">
            <div className="empty-icon" aria-hidden="true">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2" y="2" width="20" height="20" rx="3" />
                <path d="M8 16l3-4 2 2 4-5 3 4" />
                <circle cx="8" cy="8" r="1.5" />
              </svg>
            </div>
            <p>No slideshows yet. Upload some images above to get started.</p>
          </div>
        )}
        <ul className="jobs" aria-label="Job history">
          {jobs.map((j) => (
            <li key={j.id} className="job">
              <div className="job-info">
                <div className="job-meta">
                  <strong>{j.options.transition || "cut"}</strong> · {j.options.delay ?? 5}s ·{" "}
                  {j.options.resolution || "1920x1080"}
                  {j.options.ken_burns ? " · KB" : ""}
                  {j.options.autorotate ? " · ROT" : " · no-rot"} ·{" "}
                  <span className={`status-badge status-${j.status}`} aria-label={`Status: ${j.status}`}>{j.status}</span>
                </div>
                <div className="job-time">{relativeTime(j.created_at)}</div>
                {j.status === "error" && j.error && <div className="err small">{j.error}</div>}
              </div>
              <div className="job-actions">
                {j.status === "processing" && (
                  <button className="btn-cancel" onClick={() => onCancel(j.id)} aria-label="Cancel this job">
                    Cancel
                  </button>
                )}
                {j.download_url && (
                  <>
                    <button
                      className="btn-secondary btn-sm"
                      onClick={() => setPreviewSrc(j.download_url)}
                      aria-label="Preview video"
                    >
                      Preview
                    </button>
                    <a href={j.download_url} className="btn-primary btn-sm" aria-label="Download video">
                      Download
                    </a>
                  </>
                )}
                <button className="btn-danger-text" onClick={() => setDeleteTarget(j)} aria-label="Delete job">
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <ConfirmModal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) onDelete(deleteTarget);
        }}
        title="Delete job"
        message={`Are you sure you want to delete this ${deleteTarget?.status} job? This cannot be undone.`}
      />

      <VideoPreviewModal
        open={previewSrc !== null}
        onClose={() => setPreviewSrc(null)}
        src={previewSrc}
      />

      <ImagePreviewModal
        open={imagePreview !== null}
        onClose={() => setImagePreview(null)}
        src={imagePreview?.src || null}
        alt={imagePreview?.alt || ""}
      />
    </div>
  );
}

export default function App(props: Props) {
  return (
    <ToastProvider>
      <AppInner {...props} />
    </ToastProvider>
  );
}
