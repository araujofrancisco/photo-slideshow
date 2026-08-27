import { useCallback, useState } from "react";

interface DropZoneProps {
  files: File[];
  onFiles: (files: File[]) => void;
}

export default function DropZone({ files, onFiles }: DropZoneProps) {
  const [dragging, setDragging] = useState(false);

  const handleFiles = useCallback(
    (input: FileList | null) => {
      if (!input) return;
      const imageFiles = Array.from(input).filter((f) => f.type.startsWith("image/"));
      if (imageFiles.length > 0) {
        onFiles([...files, ...imageFiles]);
      }
    },
    [files, onFiles],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  return (
    <div
      className={`dropzone ${dragging ? "dropzone-active" : ""} ${files.length > 0 ? "dropzone-has-files" : ""}`}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onClick={() => document.getElementById("file-input")?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          document.getElementById("file-input")?.click();
        }
      }}
      aria-label="Drop images here or click to browse"
    >
      <input
        id="file-input"
        type="file"
        accept="image/*"
        multiple
        className="dropzone-input"
        onChange={(e) => handleFiles(e.target.files)}
      />
      {files.length === 0 ? (
        <div className="dropzone-empty">
          <div className="dropzone-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M12 16V4m0 0L8 8m4-4l4 4" />
              <path d="M20 16.7c1.2-1 2-2.5 2-4.2 0-3-2.7-5.5-6-5.5s-6 2.5-6 5.5c0 1.7.8 3.2 2 4.2" />
              <rect x="2" y="2" width="20" height="20" rx="3" />
            </svg>
          </div>
          <p className="dropzone-label">
            Drag & drop images here, or <span className="dropzone-browse">browse</span>
          </p>
          <p className="dropzone-hint">PNG, JPG, WebP, BMP, GIF, TIFF</p>
        </div>
      ) : (
        <div className="dropzone-count">
          <span className="dropzone-count-num">{files.length}</span> image{files.length !== 1 ? "s" : ""} selected — drop more or{" "}
          <span className="dropzone-browse">browse</span>
        </div>
      )}
    </div>
  );
}
