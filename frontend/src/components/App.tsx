import { useState, useEffect, useRef } from "react";
import "./styles.css";

type JobStatus = "queued" | "processing" | "done" | "error";

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

export default function App({ encoderChoices }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [delay, setDelay] = useState(5);
  const [transition, setTransition] = useState("cut");
  const [crossfade, setCrossfade] = useState(1);
  const [resolution, setResolution] = useState("1920x1080");
  const [kenBurns, setKenBurns] = useState(false);
  const [encoder, setEncoder] = useState("auto");

  const [status, setStatus] = useState<JobStatus | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const pollRef = useRef<number | null>(null);

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
      if (j.status === "done" || j.status === "error") {
        if (pollRef.current) window.clearInterval(pollRef.current);
        setError(j.error || null);
        if (j.status === "done") setMessage("Render complete!");
        loadJobs();
      }
    }, 1000);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    if (files.length === 0) {
      setError("Please choose at least one image.");
      return;
    }
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("delay", String(delay));
    fd.append("transition", transition);
    fd.append("crossfade", String(crossfade));
    fd.append("resolution", resolution);
    fd.append("ken_burns", String(kenBurns));
    fd.append("encoder", encoder);

    const res = await fetch("/api/render", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      setError(err.detail || "Failed to start render.");
      return;
    }
    const data = await res.json();
    setStatus("processing");
    setProgress(0);
    poll(data.job_id);
  };

  const onDelete = async (id: string) => {
    await fetch(`/api/jobs/${id}`, { method: "DELETE" });
    loadJobs();
  };

  return (
    <div className="app">
      <form className="card" onSubmit={onSubmit}>
        <label className="field">
          <span>Images</span>
          <input
            type="file"
            accept="image/*"
            multiple
            onChange={(e) => setFiles(Array.from(e.target.files || []))}
          />
          <small>{files.length} file(s) selected</small>
        </label>

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
            <select
              value={transition}
              onChange={(e) => setTransition(e.target.value)}
            >
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
            <input
              type="text"
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Encoder</span>
            <select
              value={encoder}
              onChange={(e) => setEncoder(e.target.value)}
            >
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

        <button type="submit" className="primary">
          Create slideshow
        </button>
      </form>

      {(status || error || message) && (
        <div className="card status">
          {error && <p className="err">{error}</p>}
          {message && <p className="ok">{message}</p>}
          {status && (
            <div className="progress-wrap">
              <div className="progress-label">
                <span>Status: {status}</span>
                <span>{Math.round(progress)}%</span>
              </div>
              <div className="progress-bar">
                <div
                  className="progress-fill"
                  style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      <div className="card">
        <h2>History</h2>
        {jobs.length === 0 && <p className="muted">No jobs yet.</p>}
        <ul className="jobs">
          {jobs.map((j) => (
            <li key={j.id} className="job">
              <div>
                <strong>{j.options.transition || "cut"}</strong> ·{" "}
                {j.options.delay ?? 5}s · {j.options.resolution || "1920x1080"}
                {j.options.ken_burns ? " · KB" : ""} · {j.status}
                {j.status === "error" && j.error ? (
                  <div className="err small">{j.error}</div>
                ) : null}
              </div>
              <div className="job-actions">
                {j.download_url ? (
                  <a href={j.download_url}>Download</a>
                ) : null}
                <button className="link" onClick={() => onDelete(j.id)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
