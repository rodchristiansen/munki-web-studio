import { useEffect, useState } from "react";
import { api, type GitInfo, type PublishingInfo, type OperationInfo, type RepositoryInfo, type WorkerView } from "../api/client";

const REFRESH_MS = 10_000;

interface Props {
  repo: RepositoryInfo | undefined;
  git?: GitInfo | null;
  canPush?: boolean;
  onPushed?: () => void;
}

/** What this studio is connected to: its repository, its workers and the tools
 * they report, and which jobs can run because of them. Settings come from the
 * config file, so this view shows them rather than editing them. */
export function Settings({ repo, git, canPush, onPushed }: Props) {
  const [pushState, setPushState] = useState<string | null>(null);
  const [publishing, setPublishing] = useState<PublishingInfo | null>(null);
  const [publishState, setPublishState] = useState<string | null>(null);
  useEffect(() => {
    if (!repo) return;
    api
      .GET("/api/repositories/{repo_id}/publishing", { params: { path: { repo_id: repo.id } } })
      .then(({ data }) => setPublishing(data ?? null))
      .catch(() => undefined);
  }, [repo, publishState]);
  async function publishNow() {
    if (!repo) return;
    setPublishState("Starting…");
    const key = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
    const { data, error } = await api.POST("/api/repositories/{repo_id}/jobs", {
      params: { path: { repo_id: repo.id } },
      body: { operation: "repository.publish", idempotency_key: key },
    });
    setPublishState(data ? `Publishing — follow it in Activity` : String((error as { detail?: unknown } | undefined)?.detail ?? "Publishing was refused"));
  }
  async function push() {
    if (!repo) return;
    setPushState("Pushing…");
    const { data, error } = await api.POST("/api/repositories/{repo_id}/git/push", { params: { path: { repo_id: repo.id } } });
    setPushState(data ? (data.message ?? "Pushed") : String((error as { detail?: unknown } | undefined)?.detail ?? "Push failed"));
    onPushed?.();
  }
  const [workers, setWorkers] = useState<WorkerView[]>([]);
  const [operations, setOperations] = useState<OperationInfo[]>([]);

  useEffect(() => {
    const load = () => {
      api
        .GET("/api/workers")
        .then(({ data }) => data && setWorkers(data))
        .catch(() => undefined);
      api
        .GET("/api/operations")
        .then(({ data }) => data && setOperations(data))
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <article className="detail settings" aria-label="Settings">
      <section className="editor-section">
        <h3>Repository</h3>
        <div className="card-fields">
          {[
            ["Name", repo?.name ?? "None"],
            ["Saving", repo ? (repo.writable ? "Allowed" : "Read-only") : "—"],
            ["Clients read it directly", repo ? (repo.live ? "Yes" : "No") : "—"],
          ].map(([k, v]) => (
            <dl className="sample-value" key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </dl>
          ))}
          <p className="muted">These come from the studio's config file.</p>
        </div>
      </section>
      <section className="editor-section" aria-label="Publishing">
        <h3>Publishing</h3>
        <div className="card-fields">
          {publishing?.mode === "live" && (
            <p className="condition">Live on save: clients read this repository directly, so every save reaches them at once. There is no separate publish step.</p>
          )}
          {publishing?.mode === "none" && <p className="muted">No publish target is configured. Clients see whatever serves this repository's files.</p>}
          {publishing?.mode === "snapshot" && (
            <>
              {[
                ["Target", publishing.target ?? ""],
                ["Current release", publishing.current ? `${publishing.current.id.slice(0, 12)} · ${new Date(publishing.current.published_at).toLocaleString()}` : "Nothing published yet"],
                ["Files", publishing.current ? `${publishing.current.files}` : "—"],
                ["Since then", publishing.unpublished_changes ? "Changes not yet published" : "Nothing new"],
              ].map(([k, v]) => (
                <dl className="sample-value" key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </dl>
              ))}
              {canPush && (
                <div className="review-actions">
                  <button type="button" className="primary" onClick={() => void publishNow()}>
                    Publish to clients
                  </button>
                </div>
              )}
              {publishState && <p role="status">{publishState}</p>}
            </>
          )}
        </div>
      </section>
      <section className="editor-section" aria-label="Git">
        <h3>Git</h3>
        <div className="card-fields">
          {!git || !git.enabled ? (
            <p className="muted">{git?.reason === "not a Git repository" ? "This repository is not a Git working tree. Saving works the same without it." : `Git is off: ${git?.reason ?? "checking…"}`}</p>
          ) : (
            <>
              {[
                ["Branch", git.branch ?? "Detached"],
                ["Commit", git.head ? git.head.slice(0, 12) : "No commits yet"],
                ["Upstream", git.upstream ? `${git.upstream} · ${git.ahead} ahead, ${git.behind} behind` : "None"],
                ["Uncommitted", git.changed.length ? `${git.changed.length} files` : "None"],
              ].map(([k, v]) => (
                <dl className="sample-value" key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </dl>
              ))}
              {canPush && (
                <div className="review-actions">
                  <button type="button" disabled={!git.upstream || git.ahead === 0 || pushState === "Pushing…"} onClick={() => void push()}>
                    Push {git.ahead ? `${git.ahead} ${git.ahead === 1 ? "commit" : "commits"}` : ""}
                  </button>
                </div>
              )}
              {pushState && <p role="status">{pushState}</p>}
            </>
          )}
        </div>
      </section>
      <section className="editor-section">
        <h3>Workers</h3>
        {workers.length === 0 ? (
          <p className="muted">No workers are configured. Add one to the config file to run jobs that need platform tools.</p>
        ) : (
          <table className="worker-table">
            <caption className="visually-hidden">Workers and their capabilities</caption>
            <thead>
              <tr>
                <th scope="col">Worker</th>
                <th scope="col">Status</th>
                <th scope="col">System</th>
                <th scope="col">Tools</th>
                <th scope="col">Capabilities</th>
              </tr>
            </thead>
            <tbody>
              {workers.map((w) => (
                <tr key={w.id}>
                  <th scope="row">{w.id}</th>
                  <td>
                    <span className={w.online ? "online" : "offline"}>{w.online ? "Connected" : w.last_seen ? "Not connected" : "Never connected"}</span>
                  </td>
                  <td>{w.platform ? `${w.platform} ${w.os_version ?? ""}` : "—"}</td>
                  <td>
                    {Object.keys(w.tools).length
                      ? Object.entries(w.tools).map(([name, version]) => (
                          <div key={name}>
                            {name} <small>{version}</small>
                          </div>
                        ))
                      : "—"}
                  </td>
                  <td>{w.capabilities.length ? w.capabilities.join(", ") : "None"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section className="editor-section">
        <h3>Jobs</h3>
        <ul className="manifest-list">
          {operations.map((op) => (
            <li key={op.name}>
              <span>{op.title}</span>
              <small className={op.available ? "" : "condition"}>{op.available ? "Ready" : op.needs}</small>
            </li>
          ))}
        </ul>
      </section>
    </article>
  );
}
