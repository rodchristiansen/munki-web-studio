import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type JobEventInfo,
  type JobInfo,
  type OperationInfo,
} from "../api/client";
import { Icon } from "./Icon";

const LIST_POLL_MS = 3_000;
const JOB_POLL_MS = 2_000;

interface Props {
  repoId: string;
  canRun: boolean;
  selected: string | null;
  onSelect: (job: string | null) => void;
}

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
};

const finished = (job: JobInfo) =>
  ["succeeded", "failed", "cancelled"].includes(job.status);

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

/** Jobs for one repository, newest first, and the one selected with its events.
 * Jobs run on the server, so leaving or reloading the page loses nothing. */
export function Activity({ repoId, canRun, selected, onSelect }: Props) {
  const [jobs, setJobs] = useState<JobInfo[]>([]);
  const [operations, setOperations] = useState<OperationInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  const loadJobs = useCallback(async () => {
    const { data } = await api.GET("/api/repositories/{repo_id}/jobs", {
      params: { path: { repo_id: repoId } },
    });
    if (data) setJobs(data);
  }, [repoId]);

  useEffect(() => {
    void loadJobs();
    const timer = window.setInterval(() => void loadJobs(), LIST_POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadJobs]);

  useEffect(() => {
    api
      .GET("/api/operations")
      .then(({ data }) => data && setOperations(data))
      .catch(() => undefined);
  }, []);

  // One key per button press, so a retried request cannot start a second job.
  async function run(operation: string) {
    setError(null);
    const { data, error: failure } = await api.POST(
      "/api/repositories/{repo_id}/jobs",
      {
        params: { path: { repo_id: repoId } },
        body: { operation, idempotency_key: newKey() },
      },
    );
    if (data) {
      onSelect(data.id);
      await loadJobs();
    } else
      setError(
        String(
          (failure as { detail?: unknown } | undefined)?.detail ??
            "The studio refused the job",
        ),
      );
  }

  const current = selected ?? jobs[0]?.id ?? null;

  return (
    <section aria-label="Activity workspace">
      <div className="content">
        <aside className="object-list" aria-label="Jobs">
          {canRun && (
            <div className="list-tools job-actions">
              {operations.map((op) => (
                <button
                  key={op.name}
                  type="button"
                  disabled={!op.available}
                  title={
                    op.available
                      ? undefined
                      : `Needs a worker with ${op.requirements.join(", ")}`
                  }
                  onClick={() => void run(op.name)}
                >
                  {op.title}
                </button>
              ))}
            </div>
          )}
          {error && (
            <p className="condition" role="alert">
              {error}
            </p>
          )}
          {jobs.length === 0 && <p className="empty-search">No jobs yet</p>}
          {jobs.map((job) => (
            <button
              key={job.id}
              type="button"
              className="package job-row"
              aria-pressed={job.id === current}
              onClick={() => onSelect(job.id)}
            >
              <Icon name="activity" />
              <span>
                {job.title}
                <small>
                  {STATUS_LABELS[job.status] ?? job.status} ·{" "}
                  {new Date(job.created_at).toLocaleTimeString()}
                </small>
              </span>
            </button>
          ))}
        </aside>
        <article className="detail">
          {current ? (
            <JobDetail
              key={current}
              repoId={repoId}
              jobId={current}
              canRun={canRun}
              onChange={() => void loadJobs()}
            />
          ) : (
            <p className="sample-note">Select a job</p>
          )}
        </article>
      </div>
    </section>
  );
}

/** Follows one job: a live event stream while it runs, falling back to polling
 * when the stream cannot be held open. */
function JobDetail({
  repoId,
  jobId,
  canRun,
  onChange,
}: {
  repoId: string;
  jobId: string;
  canRun: boolean;
  onChange: () => void;
}) {
  const [job, setJob] = useState<JobInfo | null>(null);
  const [events, setEvents] = useState<JobEventInfo[]>([]);
  const lastId = useRef(0);

  const poll = useCallback(async () => {
    const { data } = await api.GET(
      "/api/repositories/{repo_id}/jobs/{job_id}",
      {
        params: {
          path: { repo_id: repoId, job_id: jobId },
          query: { after: lastId.current },
        },
      },
    );
    if (!data) return null;
    setJob(data);
    const fresh = data.events ?? [];
    if (fresh.length) {
      lastId.current = fresh[fresh.length - 1]?.id ?? lastId.current;
      setEvents((all) => [...all, ...fresh]);
    }
    return data;
  }, [repoId, jobId]);

  useEffect(() => {
    let stopped = false;
    let source: EventSource | null = null;
    let timer: number | undefined;

    function fallback() {
      source?.close();
      source = null;
      timer = window.setInterval(() => {
        void poll().then((data) => {
          if (data && finished(data)) {
            window.clearInterval(timer);
            onChange();
          }
        });
      }, JOB_POLL_MS);
    }

    void poll().then((data) => {
      if (stopped || !data || finished(data)) return;
      if (typeof EventSource === "undefined") return fallback();
      const url = `/api/repositories/${encodeURIComponent(repoId)}/jobs/${encodeURIComponent(jobId)}/events?after=${lastId.current}`;
      source = new EventSource(url);
      source.addEventListener("end", () => {
        source?.close();
        void poll().then(onChange);
      });
      source.onmessage = () => undefined;
      for (const kind of [
        "started",
        "progress",
        "requeued",
        "cancel-requested",
        "succeeded",
        "failed",
        "cancelled",
      ]) {
        source.addEventListener(kind, (message) => {
          const event = JSON.parse(
            (message as MessageEvent<string>).data,
          ) as JobEventInfo;
          if (event.id <= lastId.current) return;
          lastId.current = event.id;
          setEvents((all) => [...all, event]);
          setJob((j) =>
            j
              ? {
                  ...j,
                  progress: event.progress ?? j.progress,
                  message: event.message || j.message,
                  status: kind === "started" ? "running" : j.status,
                }
              : j,
          );
        });
      }
      source.onerror = () => {
        if (!stopped) fallback();
      };
    });
    return () => {
      stopped = true;
      source?.close();
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [repoId, jobId, poll, onChange]);

  async function cancel() {
    const { data } = await api.POST(
      "/api/repositories/{repo_id}/jobs/{job_id}/cancel",
      {
        params: { path: { repo_id: repoId, job_id: jobId } },
      },
    );
    if (data) setJob(data);
    onChange();
  }

  if (!job) return <p className="sample-note">Loading…</p>;
  return (
    <>
      <div className="object-title">
        <div className="app-icon" aria-hidden="true">
          <Icon name="activity" />
        </div>
        <div>
          <h2>{job.title}</h2>
          <div className="object-path">
            {STATUS_LABELS[job.status] ?? job.status} · started by{" "}
            {job.created_by_name} · attempt {job.attempts} of {job.max_attempts}
          </div>
        </div>
        {canRun && !finished(job) && (
          <button
            type="button"
            onClick={() => void cancel()}
            disabled={job.cancel_requested}
          >
            {job.cancel_requested ? "Cancelling…" : "Cancel job"}
          </button>
        )}
      </div>
      <section className="editor-section">
        <h3>Progress</h3>
        <div className="card-fields">
          <progress
            max={1}
            value={job.progress}
            aria-label={`${job.title} progress`}
          />
          <p role="status">
            {job.error && job.status !== "succeeded"
              ? job.error
              : job.message || STATUS_LABELS[job.status]}
          </p>
        </div>
      </section>
      {job.result !== null && job.result !== undefined && (
        <section className="editor-section">
          <h3>Result</h3>
          <div className="card-fields">
            {Object.entries(job.result as Record<string, unknown>).map(
              ([k, v]) => (
                <dl className="sample-value" key={k}>
                  <dt>{k.replaceAll("_", " ")}</dt>
                  <dd>
                    {typeof v === "string" && v.length === 64
                      ? v.slice(0, 12)
                      : String(v)}
                  </dd>
                </dl>
              ),
            )}
          </div>
        </section>
      )}
      <section className="editor-section">
        <h3>Events</h3>
        <ol className="manifest-list job-events">
          {events.map((e) => (
            <li key={e.id}>
              <span>{e.message}</span>
              <small>{new Date(e.at).toLocaleTimeString()}</small>
            </li>
          ))}
        </ol>
        <p className="muted">
          Runs against revision {job.input_revision.slice(0, 12)} of the
          repository.
        </p>
      </section>
    </>
  );
}
