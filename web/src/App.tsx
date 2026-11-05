import { useCallback, useEffect, useState } from "react";
import { api, setCsrfToken, type GitInfo, type ObjectDetail, type ObjectSummary, type Package, type RepositoryInfo, type SessionInfo } from "./api/client";
import { Activity } from "./components/Activity";
import { Brand } from "./components/Brand";
import { Catalogs } from "./components/Catalogs";
import { CommitDialog, type SavedChange } from "./components/CommitDialog";
import { Icon } from "./components/Icon";
import { ManifestsWorkspace } from "./components/ManifestsWorkspace";
import { PackageEditor } from "./components/PackageEditor";
import { PackageList } from "./components/PackageList";
import { ReviewDialog } from "./components/ReviewDialog";
import { Settings } from "./components/Settings";
import { useDraft } from "./draft";
import { readState, toSearch, type WorkspaceState } from "./url";

const POLL_MS = 15_000;
const EDIT_ROLES = new Set(["Editor", "Publisher", "Administrator"]);

type Load<T> = { status: "loading" } | { status: "ready"; value: T } | { status: "error"; message: string };

async function allPackages(repo: string, q: string, signal: AbortSignal): Promise<Package[]> {
  const found: Package[] = [];
  for (let offset = 0; ; offset += 500) {
    const { data, error } = await api.GET("/api/repositories/{repo_id}/packages", {
      params: { path: { repo_id: repo }, query: { q: q || undefined, limit: 500, offset } },
      signal,
    });
    if (error || !data) throw new Error("Could not load packages");
    found.push(...data.items);
    if (found.length >= data.total || data.items.length === 0) return found;
  }
}

export function App() {
  const [state, setState] = useState<WorkspaceState>(() => readState());
  const [repos, setRepos] = useState<Load<RepositoryInfo[]>>({ status: "loading" });
  const [packages, setPackages] = useState<Load<Package[]>>({ status: "loading" });
  const [invalid, setInvalid] = useState<ObjectSummary[]>([]);
  const [detail, setDetail] = useState<Load<ObjectDetail> | null>(null);

  const update = useCallback((next: Partial<WorkspaceState>, mode: "push" | "replace" = "push") => {
    setState((current) => {
      const merged = { ...current, ...next };
      const url = toSearch(merged);
      if (mode === "push") window.history.pushState(null, "", url);
      else window.history.replaceState(null, "", url);
      return merged;
    });
  }, []);

  useEffect(() => {
    const onPop = () => setState(readState());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const [session, setSession] = useState<SessionInfo | null>(null);
  useEffect(() => {
    api
      .GET("/api/session")
      .then(({ data, response }) => {
        if (response.status === 401) {
          const next = window.location.pathname + window.location.search;
          window.location.assign(`/auth/login?next=${encodeURIComponent(next)}`);
          return;
        }
        if (data) {
          setCsrfToken(data.csrf_token);
          setSession(data);
        }
      })
      .catch(() => undefined);
  }, []);

  async function signOut() {
    await api.POST("/auth/logout" as never);
    window.location.assign("/");
  }

  // Poll for scans, so edits made by other tools show up while the page is open.
  const [generation, setGeneration] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastScan, setLastScan] = useState<string | null>(null);

  const loadRepos = useCallback(async (signal?: AbortSignal) => {
    try {
      const { data } = await api.GET("/api/repositories", { signal });
      setRepos(data ? { status: "ready", value: data } : { status: "error", message: "Could not load repositories" });
    } catch {
      if (!signal?.aborted) setRepos({ status: "error", message: "The studio server is not reachable" });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadRepos(controller.signal);
    const timer = window.setInterval(() => void loadRepos(controller.signal), POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [loadRepos]);

  const repoList = repos.status === "ready" ? repos.value : [];
  const repo = repoList.find((r) => r.id === state.repo) ?? repoList[0];
  const repoId = repo?.id;

  const scan = repo?.last_scan;
  useEffect(() => {
    if (!scan) return;
    const changed = scan.changed ?? [];
    if (lastScan !== null && scan.finished_at !== lastScan && changed.length > 0) {
      const open = [readState().path, readState().manifest].filter(Boolean);
      if (changed.some((p) => open.includes(p))) setNotice("This file changed on disk and was reloaded.");
      setGeneration((g) => g + 1);
    }
    setLastScan(scan.finished_at);
  }, [scan, lastScan]);

  async function rescanNow() {
    if (!repoId) return;
    await api.POST("/api/repositories/{repo_id}/rescan", { params: { path: { repo_id: repoId } } });
    await loadRepos();
  }

  useEffect(() => {
    if (!repoId) return;
    const controller = new AbortController();
    if (generation === 0) setPackages({ status: "loading" });
    allPackages(repoId, state.q, controller.signal)
      .then((value) => setPackages({ status: "ready", value }))
      .catch((e: Error) => controller.signal.aborted || setPackages({ status: "error", message: e.message }));
    api
      .GET("/api/repositories/{repo_id}/objects", {
        params: { path: { repo_id: repoId }, query: { kind: "pkginfo", status: "invalid", q: state.q || undefined, limit: 500 } },
        signal: controller.signal,
      })
      .then(({ data }) => setInvalid(data?.items ?? []))
      .catch(() => undefined);
    return () => controller.abort();
  }, [repoId, state.q, generation]);

  // Editing needs a writable repository and at least the Editor role.
  const canEdit = Boolean(repo?.writable && session && EDIT_ROLES.has(session.role));
  const draft = useDraft(repoId, canEdit);
  const [fieldTypes, setFieldTypes] = useState<Record<string, string>>({});
  const [reviewing, setReviewing] = useState(false);
  useEffect(() => {
    if (!canEdit) return;
    api
      .GET("/api/fields")
      .then(({ data }) => data && setFieldTypes(data))
      .catch(() => undefined);
  }, [canEdit]);
  const draftCount = draft.draft?.files.length ?? 0;
  const [git, setGit] = useState<GitInfo | null>(null);
  const [committing, setCommitting] = useState<SavedChange | null>(null);
  useEffect(() => {
    if (!repoId) return;
    api
      .GET("/api/repositories/{repo_id}/git", { params: { path: { repo_id: repoId } } })
      .then(({ data }) => setGit(data ?? null))
      .catch(() => undefined);
  }, [repoId, generation]);

  const pkgs = packages.status === "ready" ? packages.value : [];
  const selectedPath = state.path ?? pkgs[0]?.versions[pkgs[0].versions.length - 1]?.path ?? null;

  useEffect(() => {
    if (!repoId || !selectedPath) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    if (generation === 0) setDetail({ status: "loading" });
    api
      .GET("/api/repositories/{repo_id}/objects/{path}", {
        params: { path: { repo_id: repoId, path: selectedPath } },
        signal: controller.signal,
      })
      .then(({ data }) => setDetail(data ? { status: "ready", value: data } : { status: "error", message: "Not found" }))
      .catch(() => controller.signal.aborted || setDetail({ status: "error", message: "Could not load the file" }));
    return () => controller.abort();
  }, [repoId, selectedPath, generation]);

  const selectedPackage = pkgs.find((p) => p.versions.some((v) => v.path === selectedPath));

  return (
    <div className="shell">
      <header className="topbar">
        <div className="repo">
          <Brand />
          <div>
            <strong>{repo?.name ?? "No repository"}</strong>
            <div>
              <small>{repo ? (repo.writable ? "Writable" : "Read-only") : "Not configured"}</small>
              {repo?.live && <small className="condition"> · Live on save</small>}
              {git?.enabled && (
                <small className="branch" title={git.head ?? undefined}>
                  {" · "}
                  {git.branch ?? "detached"}
                  {git.changed.length ? ` · ${git.changed.length} uncommitted` : ""}
                </small>
              )}
            </div>
          </div>
        </div>
        <div className="workspace-title">
          <strong>{state.area === "packages" ? "Packages" : state.area === "manifests" ? "Manifests" : state.area === "catalogs" ? "Catalogs" : state.area === "activity" ? "Activity" : "Settings"}</strong>
          <small>{state.area === "packages" && packages.status === "ready" ? `${pkgs.length} packages` : " "}</small>
        </div>
        <div className="top-actions">
          {repoList.length > 1 && (
            <label className="switcher">
              Repository
              <select value={repo?.id} onChange={(e) => update({ repo: e.target.value, path: null })}>
                {repoList.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {canEdit ? (
            draftCount > 0 ? (
              <button type="button" className="draft-pill" onClick={() => setReviewing(true)}>
                Review draft · {draftCount} {draftCount === 1 ? "file" : "files"}
              </button>
            ) : (
              <span className="pill">No unsaved edits</span>
            )
          ) : (
            <span className="pill">Read-only</span>
          )}
        </div>
      </header>
      <div className="layout">
        <nav className="rail" aria-label="Work areas">
          <button type="button" aria-current={state.area === "packages" ? "page" : undefined} onClick={() => update({ area: "packages", q: "" })}>
            <Icon name="package" />
            Packages
          </button>
          <button type="button" aria-current={state.area === "manifests" ? "page" : undefined} onClick={() => update({ area: "manifests", q: "" })}>
            <Icon name="manifest" />
            Manifests
          </button>
          <button type="button" aria-current={state.area === "catalogs" ? "page" : undefined} onClick={() => update({ area: "catalogs", q: "" })}>
            <Icon name="grid" />
            Catalogs
          </button>
          <button type="button" aria-current={state.area === "activity" ? "page" : undefined} onClick={() => update({ area: "activity", q: "" })}>
            <Icon name="activity" />
            Activity
          </button>
          <button type="button" aria-current={state.area === "settings" ? "page" : undefined} onClick={() => update({ area: "settings", q: "" })}>
            <Icon name="grid" />
            Settings
          </button>
          <div className="rail-footer">
            <span className="connection-dot" /> {repo ? repo.name : "No repository"}
            <br />
            <small>{repo?.last_scan ? `Scanned ${new Date(repo.last_scan.finished_at).toLocaleTimeString()}` : "Not scanned"}</small>
            <br />
            <button type="button" className="link rescan" onClick={() => void rescanNow()} disabled={!repoId}>
              Rescan now
            </button>
            {session && session.sign_in === "oidc" && (
              <>
                <br />
                <span className="signed-in">
                  {session.name} · {session.role}
                </span>
                <br />
                <button type="button" className="link rescan" onClick={() => void signOut()}>
                  Sign out
                </button>
              </>
            )}
          </div>
        </nav>
        <div className="workspace">
          {draft.error && (
            <div className="notice" role="alert">
              {draft.error}
            </div>
          )}
          {notice && (
            <div className="notice" role="status">
              {notice}
              <button type="button" className="link" onClick={() => setNotice(null)}>
                Dismiss
              </button>
            </div>
          )}
          {repos.status === "error" ? (
            <Message title="Cannot reach the studio" text={repos.message} />
          ) : repos.status === "ready" && !repo ? (
            <Message title="No repository is configured" text="Add a repository to the config file and restart the studio." />
          ) : state.area === "catalogs" && repo ? (
            <Catalogs
              repo={repo}
              canRun={Boolean(session && EDIT_ROLES.has(session.role))}
              selected={state.catalog}
              generation={generation}
              onSelect={(catalog) => update({ catalog })}
              onOpenPackage={(path) => update({ area: "packages", q: "", path })}
              onOpenJob={(job) => update({ area: "activity", job })}
            />
          ) : state.area === "settings" ? (
            <Settings repo={repo} git={git} canPush={Boolean(session && ["Publisher", "Administrator"].includes(session.role))} onPushed={() => setGeneration((g) => g + 1)} />
          ) : state.area === "activity" && repoId ? (
            <Activity
              repoId={repoId}
              canRun={Boolean(session && EDIT_ROLES.has(session.role))}
              selected={state.job}
              onSelect={(job) => update({ job })}
            />
          ) : state.area === "manifests" && repoId ? (
            <ManifestsWorkspace
              repoId={repoId}
              generation={generation}
              selected={state.manifest}
              query={state.q}
              onSelect={(manifest) => update({ manifest })}
              canEdit={canEdit}
              fileDraft={draft.fileDraft}
              onEdit={(path, base, sections) => void draft.editSections(path, base, sections)}
              onQuery={(q) => update({ q }, "replace")}
              onOpenPackage={(name) => {
                const pkg = pkgs.find((p) => p.name === name);
                const path = pkg?.versions[pkg.versions.length - 1]?.path ?? null;
                update({ area: "packages", q: "", path });
              }}
            />
          ) : (
            <section aria-label="Package workspace">
              <div className="content">
                <PackageList
                  packages={pkgs}
                  invalid={invalid}
                  group={state.group}
                  query={state.q}
                  selectedPath={selectedPath}
                  onGroup={(group) => update({ group })}
                  onQuery={(q) => update({ q }, "replace")}
                  onSelect={(path) => update({ path })}
                />
                {detail?.status === "ready" ? (
                  <PackageEditor
                    detail={detail.value}
                    pkg={selectedPackage}
                    onSelectVersion={(path) => update({ path })}
                    editable={canEdit && detail.value.status === "ok" ? fieldTypes : {}}
                    fileDraft={draft.fileDraft(detail.value.path)}
                    onEdit={(key, value) => {
                      const file = draft.fileDraft(detail.value.path);
                      void draft.editFields(detail.value.path, file?.base_revision ?? detail.value.revision, { [key]: value });
                    }}
                  />
                ) : (
                  <article className="detail">
                    <p className="sample-note">{detail?.status === "error" ? detail.message : detail ? "Loading…" : "Select a package"}</p>
                  </article>
                )}
              </div>
            </section>
          )}
        </div>
      </div>
      {reviewing && draft.draft && (
        <ReviewDialog
          draft={draft}
          live={Boolean(repo?.live)}
          onClose={() => setReviewing(false)}
          onSaved={(saved) => {
            const files = saved.files;
            setReviewing(false);
            setNotice(`Saved ${files} ${files === 1 ? "file" : "files"} to the repository.`);
            if (git?.enabled) setCommitting(saved);
            void rescanNow();
            setGeneration((g) => g + 1);
          }}
        />
      )}
      {committing && repoId && (
        <CommitDialog
          repoId={repoId}
          branch={git?.branch ?? null}
          saved={committing}
          onClose={() => setCommitting(null)}
          onCommitted={(commit) => {
            setCommitting(null);
            setNotice(`Committed ${commit.slice(0, 8)}${git?.branch ? ` on ${git.branch}` : ""}.`);
            setGeneration((g) => g + 1);
          }}
        />
      )}
      <footer className="statusbar">
        <span role="status">{packages.status === "error" ? packages.message : repo ? `${repo.name} · ${canEdit ? "editing" : "read-only"}` : "Waiting for a repository"}</span>
        <span>{invalid.length ? `${invalid.length} invalid files` : " "}</span>
      </footer>
    </div>
  );
}

function Message({ title, text }: { title: string; text: string }) {
  return (
    <article className="detail">
      <section className="editor-section">
        <h3>{title}</h3>
        <div className="card-fields">
          <p>{text}</p>
        </div>
      </section>
    </article>
  );
}
