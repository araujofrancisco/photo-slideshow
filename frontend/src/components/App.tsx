import { useState, useEffect, useRef } from "react";
import { ToastProvider, useToast } from "./Toast";
import { ConfirmModal, VideoPreviewModal } from "./Modal";
import DropZone from "./DropZone";
import FileList from "./FileList";
import { relativeTime, formatDuration } from "../lib/time";
import { RESOLUTION_PRESETS, CUSTOM_PRESET_VALUE, estimateRenderTime } from "../lib/presets";
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
    encoder?: string;
  };
  error?: string | null;
  download_url?: string | null;
  created_at: number;
}

interface Props {
  encoderChoices: [string, string][];
}

function AppInner({ encoderChoices }: Props) {
  const { toast } = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [delay, setDelay] = useState(5);
  const [transition, setTransition] = useState("cut");
  const [crossfade, setCrossfade] = useState(1);
  const [resolution, setResolution] = useState("1920x1080");
  const [resolutionPreset, setResolutionPreset] = useState("1920x1080");
  const [kenBurns, setKenBurns] = useState(false);
  const [encoder, setEncoder] = useState("auto");

  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [progress, setProgress] = useState(0);
  const [jobs, setJobs] = useState<Job[]>([]);
  const pollRef = useRef<number | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<Job | null>(null);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);

  const loadJobs = async () => {
    try {
      const res = await fetch("/api/jobs");
      if (res.ok) setJobs(await res.json());
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    loadJobs();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const poll = (id: string) => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      const res = await fetch(`/api/jobs/${id}`);
      if (!res.ok) return;
      const j: Job = await res.json();
      setStatus(j.status);
      setProgress(j.progress);
      if (j.status === "done" || j.status === "error" || j.status === "cancelled") {
        if (pollRef.current) window.clearInterval(pollRef.current);
        if (j.status === "done") toast("success", "Render complete!");
        else if (j.status === "error") toast("error", j.error || "Render failed");
        else toast("info", "Render cancelled");
        loadJobs();
      }
    }, 1000);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (files.length === 0) {
      toast("error", "Please add at least one image.");
      return;
    }
    setSubmitting(true);
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("delay", String(delay));
    fd.append("transition", transition);
    fd.append("crossfade", String(crossfade));
    fd.append("resolution", resolution);
    fd.append("ken_burns", String(kenBurns));
    fd.append("encoder", encoder);

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
      poll(data.job_id);
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
    await fetch(`/api/jobs/${jobId}/cancel`, { method: "DELETE" });
    toast("info", "Render cancelled");
    if (pollRef.current) window.clearInterval(pollRef.current);
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
    files.length > 0
      ? estimateRenderTime(files.length, delay, transition, crossfade)
      : null;

  return (
    <div className="app">
      <form className="card" onSubmit={onSubmit}>
        <DropZone files={files} onFiles={setFiles} />
        <FileList
          files={files}
          onReorder={setFiles}
          onRemove={(i) => setFiles(files.filter((_, idx) => idx !== i))}
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
            />
          </label>
          <label className="field">
            <span>Transition</span>
            <select value={transition} onChange={(e) => setTransition(e.target.value)}>
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
              />
            </label>
          )}
        </div>

        <div className="row">
          <label className="field">
            <span>Resolution</span>
            <select value={resolutionPreset} onChange={(e) => handlePresetChange(e.target.value)}>
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
              />
            </label>
          )}
          <label className="field">
            <span>Encoder</span>
            <select value={encoder} onChange={(e) => setEncoder(e.target.value)}>
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
            />
            <span>Ken Burns</span>
          </label>
        </div>

        {estimatedTime !== null && (
          <p className="estimate">
            Estimated render: ~{formatDuration(estimatedTime)}
          </p>
        )}

        <button type="submit" className="primary" disabled={submitting || files.length === 0}>
          {submitting ? (
            <>
              <span className="spinner" /> Uploading…
            </>
          ) : (
            "Create slideshow"
          )}
        </button>
      </form>

      {status && (
        <div className="card status">
          <div className="progress-wrap">
            <div className="progress-label">
              <span>
                Status: {status}
                {status === "processing" && (
                  <button className="btn-cancel" onClick={() => onCancel(jobs[0]?.id)}>
                    Cancel
                  </button>
                )}
              </span>
              <span>{Math.round(progress)}%</span>
            </div>
            <div className="progress-bar">
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
            <div className="empty-icon">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2" y="2" width="20" height="20" rx="3" />
                <path d="M8 16l3-4 2 2 4-5 3 4" />
                <circle cx="8" cy="8" r="1.5" />
              </svg>
            </div>
            <p>No slideshows yet. Upload some images above to get started.</p>
          </div>
        )}
        <ul className="jobs">
          {jobs.map((j) => (
            <li key={j.id} className="job">
              <div className="job-info">
                <div className="job-meta">
                  <strong>{j.options.transition || "cut"}</strong> · {j.options.delay ?? 5}s ·{" "}
                  {j.options.resolution || "1920x1080"}
                  {j.options.ken_burns ? " · KB" : ""} ·{" "}
                  <span className={`status-badge status-${j.status}`}>{j.status}</span>
                </div>
                <div className="job-time">{relativeTime(j.created_at)}</div>
                {j.status === "error" && j.error && <div className="err small">{j.error}</div>}
              </div>
              <div className="job-actions">
                {j.status === "processing" && (
                  <button className="btn-cancel" onClick={() => onCancel(j.id)}>
                    Cancel
                  </button>
                )}
                {j.download_url && (
                  <>
                    <button
                      className="btn-secondary btn-sm"
                      onClick={() => setPreviewSrc(j.download_url)}
                    >
                      Preview
                    </button>
                    <a href={j.download_url} className="btn-primary btn-sm">
                      Download
                    </a>
                  </>
                )}
                <button className="btn-danger-text" onClick={() => setDeleteTarget(j)}>
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
