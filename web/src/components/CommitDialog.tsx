import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

export interface SavedChange {
  id: string;
  title: string;
  paths: string[];
}

interface Props {
  repoId: string;
  branch: string | null;
  saved: SavedChange;
  onClose: () => void;
  onCommitted: (commit: string) => void;
}

/** Commit the files a save wrote, and only those. Hooks do not run, and pushing
 * is a separate step in Settings. */
export function CommitDialog({ repoId, branch, saved, onClose, onCommitted }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [message, setMessage] = useState(saved.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
  }, []);

  async function commit() {
    setBusy(true);
    setError(null);
    const { data, error: failure } = await api.POST("/api/repositories/{repo_id}/changesets/{change_id}/commit", {
      params: { path: { repo_id: repoId, change_id: saved.id } },
      body: { message },
    });
    setBusy(false);
    if (data) onCommitted(data.commit);
    else setError(String((failure as { detail?: unknown } | undefined)?.detail ?? "The commit was refused"));
  }

  return (
    <dialog ref={ref} aria-labelledby="commit-title" onCancel={onClose} onClose={onClose}>
      <div className="dialog-body">
        <div className="dialog-title">
          <div>
            <div className="eyebrow">Git{branch ? ` · ${branch}` : ""}</div>
            <h2 id="commit-title">Commit the saved files</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="Close commit">
            Close
          </button>
        </div>
        <ul className="manifest-list">
          {saved.paths.map((p) => (
            <li key={p}>
              <span>{p}</span>
            </li>
          ))}
        </ul>
        <label className="field">
          Commit message
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} />
        </label>
        <p className="muted">Only these files are committed. Repository hooks do not run. Push from Settings.</p>
        {error && (
          <p className="condition" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="dialog-footer">
        <button type="button" onClick={onClose}>
          Not now
        </button>
        <button type="button" className="primary" disabled={busy || !message.trim()} onClick={() => void commit()}>
          Commit
        </button>
      </div>
    </dialog>
  );
}
