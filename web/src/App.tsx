import { useCallback, useEffect, useState } from "react";
import { api, type ObjectDetail, type ObjectSummary, type Package, type RepositoryInfo } from "./api/client";
import icon from "./assets/munkistudio.png";
import { Icon } from "./components/Icon";
import { ManifestView } from "./components/ManifestView";
import { PackageEditor } from "./components/PackageEditor";
import { PackageList } from "./components/PackageList";
import { readState, toSearch, type WorkspaceState } from "./url";

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

  useEffect(() => {
    const controller = new AbortController();
    api
      .GET("/api/repositories", { signal: controller.signal })
      .then(({ data }) => setRepos(data ? { status: "ready", value: data } : { status: "error", message: "Could not load repositories" }))
      .catch(() => controller.signal.aborted || setRepos({ status: "error", message: "The studio server is not reachable" }));
    return () => controller.abort();
  }, []);

  const repoList = repos.status === "ready" ? repos.value : [];
  const repo = repoList.find((r) => r.id === state.repo) ?? repoList[0];
  const repoId = repo?.id;

  useEffect(() => {
    if (!repoId) return;
    const controller = new AbortController();
    setPackages({ status: "loading" });
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
  }, [repoId, state.q]);

  const pkgs = packages.status === "ready" ? packages.value : [];
  const selectedPath = state.path ?? pkgs[0]?.versions[pkgs[0].versions.length - 1]?.path ?? null;

  useEffect(() => {
    if (!repoId || !selectedPath) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    setDetail({ status: "loading" });
    api
      .GET("/api/repositories/{repo_id}/objects/{path}", {
        params: { path: { repo_id: repoId, path: selectedPath } },
        signal: controller.signal,
      })
      .then(({ data }) => setDetail(data ? { status: "ready", value: data } : { status: "error", message: "Not found" }))
      .catch(() => controller.signal.aborted || setDetail({ status: "error", message: "Could not load the file" }));
    return () => controller.abort();
  }, [repoId, selectedPath]);

  const selectedPackage = pkgs.find((p) => p.versions.some((v) => v.path === selectedPath));

  return (
    <div className="shell">
      <header className="topbar">
        <div className="repo">
          <img className="mark" src={icon} alt="MunkiStudio app icon" width={36} height={36} />
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
          <button type="button" aria-current={state.area === "packages" ? "page" : undefined} onClick={() => update({ area: "packages" })}>
            <Icon name="package" />
            Packages
          </button>
          <button type="button" aria-current={state.area === "manifests" ? "page" : undefined} onClick={() => update({ area: "manifests" })}>
            <Icon name="manifest" />
            Manifests
          </button>
          <div className="rail-footer">
            <span className="connection-dot" /> {repo ? repo.name : "No repository"}
            <br />
            <small>{repo?.last_scan ? `Scanned ${new Date(repo.last_scan.finished_at).toLocaleString()}` : "Not scanned"}</small>
          </div>
        </nav>
        <div className="workspace">
          {repos.status === "error" ? (
            <Message title="Cannot reach the studio" text={repos.message} />
          ) : repos.status === "ready" && !repo ? (
            <Message title="No repository is configured" text="Add a repository to the config file and restart the studio." />
          ) : state.area === "manifests" ? (
            <ManifestView />
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
