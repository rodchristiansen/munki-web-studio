import { useEffect, useState } from "react";
import { api, type ManifestSection, type ManifestView, type ObjectSummary } from "../api/client";
import { Icon } from "./Icon";

const SECTION_LABELS: Record<string, string> = {
  managed_installs: "Managed installs",
  managed_uninstalls: "Managed uninstalls",
  managed_updates: "Managed updates",
  optional_installs: "Optional installs",
  featured_items: "Featured items",
  default_installs: "Default installs",
  managed_profiles: "Managed profiles",
  managed_apps: "Managed apps",
};

interface Props {
  repoId: string;
  generation: number;
  selected: string | null;
  query: string;
  onSelect: (path: string) => void;
  onQuery: (query: string) => void;
  onOpenPackage: (name: string) => void;
}

export function ManifestsWorkspace({ repoId, generation, selected, query, onSelect, onQuery, onOpenPackage }: Props) {
  const [manifests, setManifests] = useState<ObjectSummary[]>([]);
  const [view, setView] = useState<ManifestView | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .GET("/api/repositories/{repo_id}/objects", {
        params: { path: { repo_id: repoId }, query: { kind: "manifest", q: query || undefined, limit: 500 } },
        signal: controller.signal,
      })
      .then(({ data }) => setManifests(data?.items ?? []))
      .catch(() => undefined);
    return () => controller.abort();
  }, [repoId, query, generation]);

  const current = selected ?? manifests[0]?.path ?? null;

  useEffect(() => {
    if (!current) {
      setView(null);
      return;
    }
    const controller = new AbortController();
    api
      .GET("/api/repositories/{repo_id}/manifests/{path}", {
        params: { path: { repo_id: repoId, path: current } },
        signal: controller.signal,
      })
      .then(({ data }) => setView(data ?? null))
      .catch(() => undefined);
    return () => controller.abort();
  }, [repoId, current, generation]);

  const folders = new Map<string, ObjectSummary[]>();
  for (const m of manifests) {
    const parts = m.path.split("/").slice(1, -1);
    const folder = parts.length ? parts.join("/") : "manifests";
    folders.set(folder, [...(folders.get(folder) ?? []), m]);
  }

  return (
    <section aria-label="Manifest workspace">
      <div className="content">
        <aside className="object-list" aria-label="Manifest list">
          <div className="list-tools">
            <label className="search">
              <Icon name="search" />
              <input type="search" aria-label="Filter manifests" placeholder="Filter manifests" value={query} onChange={(e) => onQuery(e.target.value)} />
            </label>
          </div>
          {manifests.length === 0 && <p className="empty-search">No matching manifests</p>}
          {[...folders].sort(([a], [b]) => a.localeCompare(b)).map(([folder, items]) => (
            <div key={folder}>
              <div className="group">
                <Icon name="folder" />
                {folder}
                <small>{items.length}</small>
              </div>
              {items.map((m) => (
                <button key={m.path} type="button" className={`package${m.status === "ok" ? "" : " invalid"}`} aria-pressed={m.path === current} onClick={() => onSelect(m.path)}>
                  <Icon name="manifest" />
                  {m.path.split("/").pop()}
                  {m.status !== "ok" && <span>Invalid</span>}
                </button>
              ))}
            </div>
          ))}
        </aside>
        <article className="manifest-content detail">{view ? <Manifest view={view} onSelect={onSelect} onOpenPackage={onOpenPackage} /> : <p className="sample-note">Select a manifest</p>}</article>
      </div>
    </section>
  );
}

function Manifest({ view, onSelect, onOpenPackage }: { view: ManifestView; onSelect: (path: string) => void; onOpenPackage: (name: string) => void }) {
  return (
    <>
      <div className="object-title">
        <div className="app-icon" aria-hidden="true">
          <Icon name="manifest" />
        </div>
        <div>
          <h2>{view.name}</h2>
          <div className="object-path">{view.path}</div>
        </div>
      </div>
      {view.status !== "ok" && (
        <section className="editor-section" role="alert">
          <h3>This file could not be read</h3>
          <div className="card-fields">
            <p>{view.diagnostic?.message}</p>
            {view.diagnostic?.line != null && <p className="muted">Line {view.diagnostic.line}</p>}
          </div>
        </section>
      )}
      {view.issues.length > 0 && (
        <section className="editor-section" aria-label="Problems">
          <h3>Problems</h3>
          <ul className="manifest-list">
            {view.issues.map((issue, i) => (
              <li key={i}>
                <span>{issue.message}</span>
                <small className={issue.severity === "error" ? "condition" : ""}>{issue.severity}</small>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="editor-section">
        <h3>Catalogs</h3>
        {view.catalogs.length ? (
          <ol>
            {view.catalogs.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ol>
        ) : (
          <p className="muted">None</p>
        )}
        <p className="muted">Order is preserved. Earlier catalogs take precedence.</p>
      </section>
      <Includes includes={view.includes} onSelect={onSelect} />
      {view.sections.map((s) => (
        <Section key={s.field} section={s} onOpenPackage={onOpenPackage} />
      ))}
      {view.conditional.length > 0 && (
        <section className="editor-section">
          <h3>Conditional items</h3>
          {view.conditional.map((block, i) => (
            <div className="card-fields conditional" key={i}>
              <p className="rule">
                <span className="condition">When</span>{" "}
                {block.condition?.rule ?? (block.condition ? `${block.condition.text} — ${block.condition.error} (column ${block.condition.column})` : "No condition")}
              </p>
              {block.condition?.unknown_facts.length ? (
                <p className="muted">Needs facts from a condition script: {block.condition.unknown_facts.join(", ")}</p>
              ) : null}
              <Includes includes={block.includes} onSelect={onSelect} />
              {block.sections.map((s) => (
                <Section key={s.field} section={s} onOpenPackage={onOpenPackage} />
              ))}
            </div>
          ))}
        </section>
      )}
      <section className="editor-section">
        <h3>Included by</h3>
        {view.included_by.length ? (
          <ul className="manifest-list">
            {view.included_by.map((p) => (
              <li key={p}>
                <button type="button" className="link" onClick={() => onSelect(p)}>
                  {p}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No other manifest includes this one</p>
        )}
      </section>
    </>
  );
}

function Includes({ includes, onSelect }: { includes: ManifestView["includes"]; onSelect: (path: string) => void }) {
  if (!includes.length) return null;
  return (
    <section className="editor-section">
      <h3>Included manifests</h3>
      <ul className="manifest-list">
        {includes.map((inc) => (
          <li key={inc.name}>
            {inc.path ? (
              <button type="button" className="link" onClick={() => inc.path && onSelect(inc.path)}>
                {inc.name}
              </button>
            ) : (
              <span>{inc.name}</span>
            )}
            {!inc.path && <small className="condition">Missing</small>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Section({ section, onOpenPackage }: { section: ManifestSection; onOpenPackage: (name: string) => void }) {
  return (
    <section className="editor-section">
      <h3>{SECTION_LABELS[section.field] ?? section.field}</h3>
      <ul className="manifest-list">
        {section.items.map((item, i) => (
          <li key={`${item.name}-${i}`}>
            {item.package ? (
              <button type="button" className="link" onClick={() => item.package && onOpenPackage(item.package)}>
                {item.name}
              </button>
            ) : (
              <span>{item.name}</span>
            )}
            {!item.package && <small className="condition">No such package</small>}
          </li>
        ))}
      </ul>
    </section>
  );
}
