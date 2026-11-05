import { useCallback, useEffect, useState } from "react";
import { api, setCsrfToken, type ObjectDetail, type ObjectSummary, type Package, type RepositoryInfo, type SessionInfo } from "./api/client";
import { Brand } from "./components/Brand";
import { Icon } from "./components/Icon";
import { ManifestsWorkspace } from "./components/ManifestsWorkspace";
import { PackageEditor } from "./components/PackageEditor";
import { PackageList } from "./components/PackageList";
import { readState, toSearch, type WorkspaceState } from "./url";

const POLL_MS = 15_000;

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
            </div>
          </div>
        </div>
        <div className="workspace-title">
          <strong>{state.area === "packages" ? "Packages" : "Manifests"}</strong>
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
          <span className="pill">Read-only</span>
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
          ) : state.area === "manifests" && repoId ? (
            <ManifestsWorkspace
              repoId={repoId}
              generation={generation}
              selected={state.manifest}
              query={state.q}
              onSelect={(manifest) => update({ manifest })}
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
                  <PackageEditor detail={detail.value} pkg={selectedPackage} onSelectVersion={(path) => update({ path })} />
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
      <footer className="statusbar">
        <span role="status">{packages.status === "error" ? packages.message : repo ? `${repo.name} · read-only` : "Waiting for a repository"}</span>
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
