import { useEffect, useState } from "react";
import { api, type OperationInfo, type RepositoryInfo, type WorkerView } from "../api/client";

const REFRESH_MS = 10_000;

interface Props {
  repo: RepositoryInfo | undefined;
}

/** What this studio is connected to: its repository, its workers and the tools
 * they report, and which jobs can run because of them. Settings come from the
 * config file, so this view shows them rather than editing them. */
export function Settings({ repo }: Props) {
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
