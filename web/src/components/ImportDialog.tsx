import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { IMPORT_ACCEPT } from "../platform";

const CHUNK_BYTES = 8 * 1024 * 1024;
// WebCrypto hashes a whole buffer at once, so larger files go through the CLI.
const MAX_BROWSER_BYTES = 2 * 1024 * 1024 * 1024;
const RETRIES = 5;

interface Props {
  repoId: string;
  available: boolean;
  needs: string | null;
  onClose: () => void;
  onDrafted: (changeSet: string) => void;
}

type Stage = { kind: "choose" } | { kind: "working"; message: string; progress: number } | { kind: "error"; message: string };

function hex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function resumeKey(repoId: string, file: File): string {
  return `upload:${repoId}:${file.name}:${file.size}:${file.lastModified}`;
}

function remember(key: string, value: string | null) {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    // Private windows can refuse storage; the upload simply starts over then.
  }
}

function recall(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

const detail = (error: unknown, fallback: string) => String((error as { detail?: unknown } | undefined)?.detail ?? fallback);

/** Upload an installer in resumable chunks, run makepkginfo on the worker, and
 * hand back the draft it produces. Nothing reaches the repository until that
 * draft is reviewed and saved. */
export function ImportDialog({ repoId, available, needs, onClose, onDrafted }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [folder, setFolder] = useState("apps");
  const [stage, setStage] = useState<Stage>({ kind: "choose" });

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
  }, []);

  async function send(chosen: File): Promise<string> {
    const key = resumeKey(repoId, chosen);
    let uploadId = recall(key);
    let received = 0;
    if (uploadId) {
      const { data } = await api.GET("/api/repositories/{repo_id}/uploads/{upload_id}", { params: { path: { repo_id: repoId, upload_id: uploadId } } });
      if (data && !data.complete) received = data.received;
      else if (data?.complete) return uploadId;
      else uploadId = null;
    }
    if (!uploadId) {
      setStage({ kind: "working", message: "Checking the file", progress: 0 });
      const sha256 = hex(await crypto.subtle.digest("SHA-256", await chosen.arrayBuffer()));
      const { data, error } = await api.POST("/api/repositories/{repo_id}/uploads", {
        params: { path: { repo_id: repoId } },
        body: { filename: chosen.name, size: chosen.size, sha256 },
      });
      if (!data) throw new Error(detail(error, "The studio refused the upload"));
      uploadId = data.id;
      remember(key, uploadId);
    }
    let failures = 0;
    while (received < chosen.size) {
      const chunk = chosen.slice(received, received + CHUNK_BYTES);
      setStage({ kind: "working", message: `Uploading ${chosen.name}`, progress: received / chosen.size });
      try {
        const url = `${window.location.origin}/api/repositories/${encodeURIComponent(repoId)}/uploads/${uploadId}`;
        const reply = await fetch(
          new Request(url, {
            method: "PATCH",
            headers: { "Upload-Offset": String(received), "Content-Type": "application/octet-stream", "X-CSRF-Token": csrf() },
            body: chunk,
          }),
        );
        const body = (await reply.json()) as { received?: number; detail?: string };
        if (reply.status === 409 || reply.ok) {
          // A 409 means the studio has a different offset: ask it and carry on.
          const { data } = await api.GET("/api/repositories/{repo_id}/uploads/{upload_id}", { params: { path: { repo_id: repoId, upload_id: uploadId } } });
          received = data?.received ?? body.received ?? received;
          failures = 0;
          continue;
        }
        throw new Error(body.detail ?? `Upload failed (${reply.status})`);
      } catch (error) {
        failures += 1;
        if (failures > RETRIES) throw error;
        await new Promise((resolve) => setTimeout(resolve, 500 * failures));
      }
    }
    remember(key, null);
    return uploadId;
  }

  async function start() {
    if (!file) return;
    if (file.size > MAX_BROWSER_BYTES) {
      setStage({ kind: "error", message: "This file is over 2 GB; import it with the command line instead." });
      return;
    }
    try {
      const uploadId = await send(file);
      setStage({ kind: "working", message: "Reading the package on the worker", progress: 1 });
      const { data, error } = await api.POST("/api/repositories/{repo_id}/jobs", {
        params: { path: { repo_id: repoId } },
        body: { operation: "package.import", arguments: { upload: uploadId, subdirectory: folder.trim() }, idempotency_key: uploadId },
      });
      if (!data) throw new Error(detail(error, "The studio refused the import"));
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const { data: job } = await api.GET("/api/repositories/{repo_id}/jobs/{job_id}", { params: { path: { repo_id: repoId, job_id: data.id } } });
        if (!job || ["queued", "running"].includes(job.status)) continue;
        if (job.status !== "succeeded") throw new Error(job.error ?? "The import failed");
        const drafted = (job.result as { draft?: { change_set: string } } | null)?.draft;
        if (!drafted) throw new Error("The import produced no draft");
        onDrafted(drafted.change_set);
        return;
      }
    } catch (error) {
      setStage({ kind: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }

  const working = stage.kind === "working";
  return (
    <dialog ref={ref} aria-labelledby="import-title" onCancel={onClose} onClose={onClose}>
      <div className="dialog-body">
        <div className="dialog-title">
          <div>
            <div className="eyebrow">Import</div>
            <h2 id="import-title">Import a package</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="Close import">
            Close
          </button>
        </div>
        {!available && <p className="condition">{needs ?? "No worker can read packages right now."}</p>}
        <label className="field">
          Installer
          <input type="file" accept={IMPORT_ACCEPT} disabled={working} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label className="field">
          Folder under pkgs and pkgsinfo
          <input value={folder} disabled={working} onChange={(e) => setFolder(e.target.value)} placeholder="apps" />
        </label>
        {stage.kind === "working" && (
          <>
            <progress max={1} value={stage.progress} aria-label="Import progress" />
            <p role="status">{stage.message}</p>
          </>
        )}
        {stage.kind === "error" && (
          <p className="condition" role="alert">
            {stage.message}
          </p>
        )}
        <p className="muted">The worker reads the package with makepkginfo. The result is a draft to review; nothing is saved until you save it.</p>
      </div>
      <div className="dialog-footer">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="primary" disabled={!file || !available || working} onClick={() => void start()}>
          Upload and import
        </button>
      </div>
    </dialog>
  );
}

let token = "";
export function setImportCsrf(value: string) {
  token = value;
}
function csrf(): string {
  return token;
}
