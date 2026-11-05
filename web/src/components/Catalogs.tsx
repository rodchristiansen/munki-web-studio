import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type CatalogDetail,
  type CatalogSummary,
  type JobInfo,
  type OperationInfo,
  type RepositoryInfo,
} from "../api/client";
import { Icon } from "./Icon";

const GENERATE = "catalog.generate";
const RUNNING_POLL_MS = 2_000;

interface Props {
  repo: RepositoryInfo;
  canRun: boolean;
  selected: string | null;
  generation: number;
  onSelect: (name: string) => void;
  onOpenPackage: (path: string) => void;
  onOpenJob: (id: string) => void;
}

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

const finished = (job: JobInfo) =>
  ["succeeded", "failed", "cancelled"].includes(job.status);

/** Catalogs are built from pkginfo, so this view shows what each one holds and
 * where it has fallen behind, and runs the generation job; it never edits them. */
export function Catalogs({
  repo,
  canRun,
  selected,
  generation,
  onSelect,
  onOpenPackage,
  onOpenJob,
}: Props) {
  const [catalogs, setCatalogs] = useState<CatalogSummary[]>([]);
  const [detail, setDetail] = useState<CatalogDetail | null>(null);
  const [operation, setOperation] = useState<OperationInfo | null>(null);
  const [lastJob, setLastJob] = useState<JobInfo | null>(null);
  const [confirmLive, setConfirmLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  // When a generation this page was following finishes, reload the catalogs.
  const following = useRef<JobInfo | null>(null);
  const loadJob = useCallback(async () => {
    const { data } = await api.GET("/api/repositories/{repo_id}/jobs", { params: { path: { repo_id: repo.id } } });
    const job = data?.find((j) => j.operation === GENERATE) ?? null;
    const previous = following.current;
    following.current = job;
    setLastJob(job);
    if (previous && !finished(previous) && job && finished(job)) setRefresh((r) => r + 1);
    return job;
  }, [repo.id]);

  useEffect(() => {
    api
      .GET("/api/repositories/{repo_id}/catalogs", {
        params: { path: { repo_id: repo.id } },
      })
      .then(({ data }) => data && setCatalogs(data))
      .catch(() => undefined);
    api
      .GET("/api/operations")
      .then(({ data }) =>
        setOperation(data?.find((o) => o.name === GENERATE) ?? null),
      )
      .catch(() => undefined);
    void loadJob();
  }, [repo.id, generation, refresh, loadJob]);

  const current = selected ?? catalogs[0]?.name ?? null;

  useEffect(() => {
    if (!current) {
      setDetail(null);
      return;
    }
    api
      .GET("/api/repositories/{repo_id}/catalogs/{name}", {
        params: { path: { repo_id: repo.id, name: current } },
      })
      .then(({ data }) => setDetail(data ?? null))
      .catch(() => undefined);
  }, [repo.id, current, generation, refresh]);

  // Follow a running generation until it finishes, then reload the catalogs.
  useEffect(() => {
    if (!lastJob || finished(lastJob)) return;
    const timer = window.setInterval(() => void loadJob(), RUNNING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [lastJob, loadJob]);

  async function generate() {
    setError(null);
    const { data, error: failure } = await api.POST(
      "/api/repositories/{repo_id}/jobs",
      {
        params: { path: { repo_id: repo.id } },
        body: {
          operation: GENERATE,
          idempotency_key: newKey(),
          arguments: repo.live ? { acknowledge_live: confirmLive } : {},
        },
      },
    );
    if (data) {
      following.current = data;
      setLastJob(data);
    }
    else
      setError(
        String(
          (failure as { detail?: unknown } | undefined)?.detail ??
            "The studio refused the job",
        ),
      );
  }

  const behind = catalogs.filter((c) => !c.up_to_date).length;
  const running = lastJob !== null && !finished(lastJob);
  const blockedReason = !repo.writable
    ? "This repository is read-only in the studio's configuration."
    : operation && !operation.available
      ? operation.needs
      : null;

  return (
    <section aria-label="Catalogs workspace">
      <div className="content">
        <aside className="object-list" aria-label="Catalog list">
          <div className="list-tools generation">
            <p role="status" className={behind ? "condition" : "muted"}>
              {behind
                ? `${behind} ${behind === 1 ? "catalog is" : "catalogs are"} out of date`
                : "Catalogs match the pkginfo"}
            </p>
            {canRun && (
              <>
                {repo.live && (
                  <label className="live-confirm">
                    <input
                      type="checkbox"
                      checked={confirmLive}
                      onChange={(e) => setConfirmLive(e.target.checked)}
                    />
                    Clients see new catalogs at once
                  </label>
                )}
                <button
                  type="button"
                  className="primary"
                  disabled={
                    running ||
                    Boolean(blockedReason) ||
                    (repo.live && !confirmLive)
                  }
                  aria-describedby={
                    blockedReason ? "generate-blocked" : undefined
                  }
                  onClick={() => void generate()}
                >
                  {running ? "Generating…" : "Generate catalogs"}
                </button>
                {blockedReason && (
                  <p id="generate-blocked" className="muted needs">
                    {blockedReason}
                  </p>
                )}
              </>
            )}
            {lastJob && (
              <p className="muted">
                Last run:{" "}
                <button
                  type="button"
                  className="link"
                  onClick={() => onOpenJob(lastJob.id)}
                >
                  {lastJob.status === "succeeded" ? "finished" : lastJob.status}{" "}
                  {new Date(lastJob.updated_at).toLocaleString()}
                </button>
                {lastJob.status === "failed" && lastJob.error
                  ? ` — ${lastJob.error}`
                  : ""}
              </p>
            )}
            {error && (
              <p className="condition" role="alert">
                {error}
              </p>
            )}
          </div>
          {catalogs.map((c) => (
            <button
              key={c.name}
              type="button"
              className="package"
              aria-pressed={c.name === current}
              onClick={() => onSelect(c.name)}
            >
              <Icon name="grid" />
              {c.name}
              <span className={c.up_to_date ? "muted" : "condition"}>
                {c.path === null
                  ? "Not generated"
                  : c.up_to_date
                    ? `${c.items}`
                    : "Out of date"}
              </span>
            </button>
          ))}
        </aside>
        <article className="detail">
          {detail ? (
            <Catalog detail={detail} onOpenPackage={onOpenPackage} />
          ) : (
            <p className="sample-note">Select a catalog</p>
          )}
        </article>
      </div>
    </section>
  );
}

function Catalog({
  detail,
  onOpenPackage,
}: {
  detail: CatalogDetail;
  onOpenPackage: (path: string) => void;
}) {
  const item = (m: CatalogDetail["members"][number], i: number) => (
    <li key={`${m.name}-${m.version}-${i}`}>
      {m.pkginfo ? (
        <button
          type="button"
          className="link"
          onClick={() => m.pkginfo && onOpenPackage(m.pkginfo)}
        >
          {m.name}
        </button>
      ) : (
        <span>{m.name}</span>
      )}
      <small>{m.version}</small>
    </li>
  );
  return (
    <>
      <div className="object-title">
        <div className="app-icon" aria-hidden="true">
          <Icon name="grid" />
        </div>
        <div>
          <h2>{detail.name}</h2>
          <div className="object-path">
            {detail.path ?? "Not generated yet"}
          </div>
        </div>
      </div>
      {!detail.up_to_date && (
        <section className="editor-section" aria-label="Out of date">
          <h3>Out of date</h3>
          {detail.error && detail.path && (
            <p className="condition">{detail.error}</p>
          )}
          {detail.missing.length > 0 && (
            <>
              <p className="muted">
                Listed by pkginfo but missing from this catalog
              </p>
              <ul className="manifest-list">{detail.missing.map(item)}</ul>
            </>
          )}
          {detail.extra.length > 0 && (
            <>
              <p className="muted">
                In this catalog but no pkginfo puts it there
              </p>
              <ul className="manifest-list">{detail.extra.map(item)}</ul>
            </>
          )}
          <p className="muted">Generating catalogs brings this up to date.</p>
        </section>
      )}
      <section className="editor-section">
        <h3>Members ({detail.members.length})</h3>
        {detail.members.length ? (
          <ul className="manifest-list">{detail.members.map(item)}</ul>
        ) : (
          <p className="muted">None</p>
        )}
      </section>
    </>
  );
}
