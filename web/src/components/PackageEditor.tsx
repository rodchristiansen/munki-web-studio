import { useRef, useState } from "react";
import type { ObjectDetail, Package } from "../api/client";
import { TABS, type FieldSpec } from "../platform";
import { decode, type Value } from "../tagged";
import { asDict, items, show, type Dict } from "../values";
import { Icon } from "./Icon";

interface Props {
  detail: ObjectDetail;
  pkg: Package | undefined;
  onSelectVersion: (path: string) => void;
}

const LISTED = new Set(TABS.flatMap((t) => t.sections.flatMap((s) => s.fields.map((f) => f.key))));

export function PackageEditor({ detail, pkg, onSelectVersion }: Props) {
  const [tab, setTab] = useState(TABS[0]?.name ?? "Overview");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const data = asDict(detail.data === null || detail.data === undefined ? undefined : decode(detail.data));
  const names = TABS.map((t) => t.name);

  function onTabKey(event: React.KeyboardEvent, index: number) {
    const next =
      event.key === "ArrowRight" ? (index + 1) % names.length
      : event.key === "ArrowLeft" ? (index - 1 + names.length) % names.length
      : event.key === "Home" ? 0
      : event.key === "End" ? names.length - 1
      : undefined;
    if (next === undefined) return;
    event.preventDefault();
    setTab(names[next] ?? names[0] ?? "Overview");
    tabRefs.current[next]?.focus();
  }

  const id = (t: string) => t.toLowerCase();
  const title = show(data.get("name")) !== "Not set" ? show(data.get("name")) : detail.path.split("/").pop();

  return (
    <article className="detail">
      <div className="object-title">
        <div className="app-icon" aria-hidden="true">
          <Icon name="package" />
        </div>
        <div>
          <h2>{title}</h2>
          <div className="object-path">{detail.path}</div>
        </div>
        {pkg && pkg.versions.length > 1 && (
          <label className="version-picker">
            Version
            <select value={detail.path} onChange={(e) => onSelectVersion(e.target.value)}>
              {pkg.versions.map((v) => (
                <option key={v.path} value={v.path}>
                  {v.version ?? "No version"} · {v.path.split("/").slice(1, -1).join("/") || "pkgsinfo"}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {detail.status !== "ok" ? (
        <section className="editor-section" role="alert">
          <h3>This file could not be read</h3>
          <div className="card-fields">
            <p>{detail.diagnostic?.message ?? "Unknown error"}</p>
            {detail.diagnostic?.line != null && (
              <p className="muted">
                Line {detail.diagnostic.line}
                {detail.diagnostic.column != null ? `, column ${detail.diagnostic.column}` : ""}
              </p>
            )}
          </div>
        </section>
      ) : (
        <>
          <div className="tabs" role="tablist" aria-label="Package editor">
            {names.map((t, i) => (
              <button
                key={t}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`tab-${id(t)}`}
                aria-controls={`panel-${id(t)}`}
                aria-selected={tab === t}
                tabIndex={tab === t ? 0 : -1}
                onClick={() => setTab(t)}
                onKeyDown={(e) => onTabKey(e, i)}
              >
                {t}
              </button>
            ))}
          </div>
          {TABS.map((t) => (
            <div key={t.name} id={`panel-${id(t.name)}`} role="tabpanel" aria-labelledby={`tab-${id(t.name)}`} hidden={tab !== t.name}>
              <div className="form-grid">
                {t.sections.map((section) => (
                  <section className="editor-section" key={section.title}>
                    <h3>{section.title}</h3>
                    <div className="card-fields">
                      {section.fields.map((f) => (
                        <Field key={f.key} spec={f} value={data.get(f.key)} />
                      ))}
                    </div>
                  </section>
                ))}
                {t.name === "Advanced" && <Remaining data={data} detail={detail} />}
              </div>
            </div>
          ))}
        </>
      )}
    </article>
  );
}

function Field({ spec, value }: { spec: FieldSpec; value: Value | undefined }) {
  if (spec.kind === "script") {
    return (
      <label className="field">
        {spec.label}
        <textarea value={value === undefined ? "" : show(value)} placeholder="Not set" readOnly />
      </label>
    );
  }
  if (spec.kind === "list") {
    const list = items(value);
    return (
      <dl className="sample-value">
        <dt>{spec.label}</dt>
        <dd>{list.length ? <ul className="value-list">{list.map((v, i) => <li key={i}>{v}</li>)}</ul> : "None"}</dd>
      </dl>
    );
  }
  return (
    <label className="field">
      {spec.label}
      <input value={value === undefined ? "" : show(value)} placeholder="Not set" readOnly />
    </label>
  );
}

function Remaining({ data, detail }: { data: Dict; detail: ObjectDetail }) {
  const rest = [...data].filter(([k]) => !LISTED.has(k));
  return (
    <>
      <section className="editor-section">
        <h3>Other keys</h3>
        <div className="card-fields">
          {rest.length === 0 && <p className="muted">None</p>}
          {rest.map(([k, v]) => (
            <dl className="sample-value" key={k}>
              <dt>{k}</dt>
              <dd>{show(v)}</dd>
            </dl>
          ))}
        </div>
      </section>
      <section className="editor-section">
        <h3>File</h3>
        <div className="card-fields">
          {[
            ["Relative path", detail.path],
            ["Format", detail.format ?? "Unknown"],
            ["Revision", detail.revision.slice(0, 12)],
          ].map(([k, v]) => (
            <dl className="sample-value" key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </dl>
          ))}
        </div>
      </section>
    </>
  );
}
