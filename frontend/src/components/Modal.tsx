import { useEffect, useRef } from "react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
}

export default function Modal({ open, onClose, title, children, actions }: ModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) {
      el.showModal();
    } else if (!open && el.open) {
      el.close();
    }
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="modal"
      onClose={onClose}
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose();
      }}
    >
      {title && <div className="modal-header">{title}</div>}
      <div className="modal-body">{children}</div>
      {actions && <div className="modal-actions">{actions}</div>}
    </dialog>
  );
}

interface ConfirmModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmLabel?: string;
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = "Delete",
}: ConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      actions={
        <>
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-danger"
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      <p>{message}</p>
    </Modal>
  );
}

interface VideoPreviewModalProps {
  open: boolean;
  onClose: () => void;
  src: string | null;
}

export function VideoPreviewModal({ open, onClose, src }: VideoPreviewModalProps) {
  return (
    <Modal open={open} onClose={onClose} title="Video Preview">
      {src ? (
        <video controls autoPlay className="video-preview">
          <source src={src} type="video/mp4" />
          Your browser does not support video playback.
        </video>
      ) : (
        <p className="muted">No video available.</p>
      )}
    </Modal>
  );
}

interface ImagePreviewModalProps {
  open: boolean;
  onClose: () => void;
  src: string | null;
  alt: string;
}

export function ImagePreviewModal({ open, onClose, src, alt }: ImagePreviewModalProps) {
  return (
    <Modal open={open} onClose={onClose} title="Image Preview">
      {src ? (
        <img src={src} alt={alt} className="image-preview" />
      ) : (
        <p className="muted">No image available.</p>
      )}
    </Modal>
  );
}
