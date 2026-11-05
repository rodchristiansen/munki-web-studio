import { useEffect, useRef, useState } from "react";
import type { Draft } from "../draft";

interface Props {
  draft: Draft;
  live: boolean;
  onClose: () => void;
  onSaved: (files: number) => void;
}

/** Every file the draft changes, with its diff, and the one place a draft is saved.
 * A file someone else changed since it was edited shows as a conflict, and saving
 * waits until each conflict is reapplied or dropped. */
export function ReviewDialog({ draft, live, onClose, onSaved }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [confirmLive, setConfirmLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const files = draft.draft?.files ?? [];
  const conflicts = files.filter((f) => f.conflict);
  const errors = files.filter((f) => f.error);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
  }, []);

  async function save() {
    setBusy(true);
    setMessage(null);
    const outcome = await draft.save(confirmLive);
    setBusy(false);
    if (outcome.kind === "saved") onSaved(outcome.files);
    else setMessage(outcome.message);
  }

  async function discard() {
    setBusy(true);
    await draft.discard();
    setBusy(false);
    onClose();
  }

  const blocked =
    files.length === 0 ||
    conflicts.length > 0 ||
    errors.length > 0 ||
    (live && !confirmLive) ||
    busy;

  return (
    <dialog
      ref={ref}
      aria-labelledby="review-title"
      onCancel={onClose}
      onClose={onClose}
    >
      <div className="dialog-body">
        <div className="dialog-title">
          <div>
            <div className="eyebrow">Review draft</div>
            <h2 id="review-title">
              {files.length} {files.length === 1 ? "file" : "files"} changed
            </h2>
          </div>
          <button type="button" onClick={onClose} aria-label="Close review">
            Close
          </button>
        </div>
        {conflicts.length > 0 && (
          <p role="alert" className="condition">
            {conflicts.length === 1 ? "A file" : `${conflicts.length} files`}{" "}
            changed on disk after you edited{" "}
            {conflicts.length === 1 ? "it" : "them"}. Reapply your edits to the
            current file or drop them before saving.
          </p>
        )}
        {message && (
          <p role="alert" className="condition">
            {message}
          </p>
        )}
        <div className="review-list">
          {files.map((file) => (
            <section
              className="review-file"
              key={file.path}
              aria-label={file.path}
            >
              <div className="review-path">
                {file.path}
                {file.conflict && (
                  <span className="badge">Changed on disk</span>
                )}
                {file.error && <span className="badge">{file.error}</span>}
                {!file.surgical && !file.error && (
                  <span className="badge">Rewritten in full</span>
                )}
              </div>
              {file.diff && <pre className="diff">{file.diff}</pre>}
              <div className="review-actions">
                {file.conflict && file.current_revision && (
                  <button
                    type="button"
                    onClick={() => void draft.reapply(file.path)}
                  >
                    Reapply to current file
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void draft.drop(file.path)}
                >
                  Drop edits to this file
                </button>
              </div>
            </section>
          ))}
        </div>
        {live && (
          <label className="live-confirm">
            <input
              type="checkbox"
              checked={confirmLive}
              onChange={(e) => setConfirmLive(e.target.checked)}
            />
            This repository is live: clients see the change as soon as it is
            saved
          </label>
        )}
      </div>
      <div className="dialog-footer">
        <button type="button" onClick={() => void discard()} disabled={busy}>
          Discard draft
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => void save()}
          disabled={blocked}
        >
          Save to repository
        </button>
      </div>
    </dialog>
  );
}
