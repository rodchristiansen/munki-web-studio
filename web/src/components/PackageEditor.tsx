import { useRef, useState } from "react";
import { type PackageRecord, installerItem, relativePath } from "../sample";
import { Icon } from "./Icon";

const TABS = ["Overview", "Detection", "Install", "Uninstall", "Advanced"] as const;
type Tab = (typeof TABS)[number];

interface Props {
  record: PackageRecord;
  onChange: (record: PackageRecord) => void;
}

export function PackageEditor({ record, onChange }: Props) {
  const [tab, setTab] = useState<Tab>("Overview");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  function onTabKey(event: React.KeyboardEvent, index: number) {
    const last = TABS.length - 1;
    const next =
      event.key === "ArrowRight" ? (index + 1) % TABS.length
      : event.key === "ArrowLeft" ? (index - 1 + TABS.length) % TABS.length
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : undefined;
    if (next === undefined) return;
    event.preventDefault();
    setTab(TABS[next] ?? "Overview");
    tabRefs.current[next]?.focus();
  }

  const id = (t: Tab) => t.toLowerCase();

  return (
    <article className="detail">
      <div className="object-title">
        <div className="app-icon" aria-hidden="true">
          <Icon name="package" />
        </div>
        <div>
          <h2>{record.name}</h2>
          <div className="object-path">{relativePath(record)}</div>
        </div>
      </div>
      <div className="tabs" role="tablist" aria-label="Package editor">
        {TABS.map((t, i) => (
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
        <div
          key={t}
          id={`panel-${id(t)}`}
          role="tabpanel"
          aria-labelledby={`tab-${id(t)}`}
          hidden={tab !== t}
        >
          {t === "Overview" ? (
            <Overview record={record} onChange={onChange} />
          ) : (
            <SamplePanel tab={t} record={record} />
          )}
        </div>
      ))}
    </article>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="editor-section">
      <h3>{title}</h3>
      <div className="card-fields">{children}</div>
    </section>
  );
}

function ReadOnly({ label, value }: { label: string; value: string }) {
  return (
    <label className="field">
      {label}
      <input value={value} readOnly />
    </label>
  );
}

function Overview({ record, onChange }: Props) {
  return (
    <div className="form-grid">
      <div>
        <Section title="Identity">
          <ReadOnly label="Name" value={record.name} />
          <label className="field">
            Display name
            <input
              value={record.displayName}
              onChange={(e) => onChange({ ...record, displayName: e.target.value })}
            />
          </label>
          <ReadOnly label="Version" value={record.version} />
          <ReadOnly label="Category" value={record.category} />
        </Section>
        <Section title="Catalogs">
          {(["testing", "production"] as const).map((catalog) => (
            <label className="check" key={catalog}>
              <input
                type="checkbox"
                checked={record[catalog]}
                onChange={(e) => onChange({ ...record, [catalog]: e.target.checked })}
              />
              {catalog === "testing" ? "Testing" : "Production"}
            </label>
          ))}
          <p className="summary-note">
            Membership changes affect availability after catalog generation and publication.
          </p>
        </Section>
        <Section title="Description">
          <label className="field">
            Shown in Managed Software Center
            <textarea
              value={record.description}
              onChange={(e) => onChange({ ...record, description: e.target.value })}
            />
          </label>
        </Section>
      </div>
      <div>
        <Section title="Installer">
          <ReadOnly label="Type" value={record.type === "Package" ? "pkg" : "copy_from_dmg"} />
          <ReadOnly label="Location" value={installerItem(record)} />
        </Section>
        <Section title="Requirements">
          <ReadOnly label="Minimum macOS" value={record.minimumOs} />
          <ReadOnly label="Architecture" value="arm64, x86_64" />
        </Section>
      </div>
    </div>
  );
}

function Values({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <Section title={title}>
      {rows.map(([label, value]) => (
        <dl className="sample-value" key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </dl>
      ))}
    </Section>
  );
}

function SamplePanel({ tab, record }: { tab: Tab; record: PackageRecord }) {
  const app = record.type !== "Package";
  const appPath = `/Applications/${record.name}.app`;
  return (
    <>
      <p className="sample-note">
        Read-only sample metadata for {record.name}. No commands or scripts are executed.
      </p>
      {tab === "Detection" && (
        <>
          <Values
            title="Install detection"
            rows={
              app ?
                [["Method", "Application bundle"], ["Path", appPath], ["Version", record.version]]
              : [
                  ["Method", "Package receipt"],
                  ["Package ID", `org.example.${record.name.toLowerCase()}`],
                  ["Version", record.version],
                ]
            }
          />
          <Values title="Check scripts" rows={[["Install check", "Not configured"], ["Version script", "Not configured"]]} />
        </>
      )}
      {tab === "Install" && (
        <Values
          title="Installer"
          rows={[
            ["Type", app ? "copy_from_dmg" : "pkg"],
            ["Location", installerItem(record)],
            ["Destination", app ? "/Applications" : "Defined by installer"],
          ]}
        />
      )}
      {tab === "Uninstall" && (
        <Values
          title="Removal"
          rows={[
            ["Uninstallable", app ? "Yes" : "Not specified"],
            ["Method", app ? "remove_copied_items" : "Not configured"],
            ["Target", app ? appPath : "None specified"],
          ]}
        />
      )}
      {tab === "Advanced" && (
        <>
          <Values
            title="Compatibility"
            rows={[["Minimum macOS", record.minimumOs], ["Architectures", "arm64, x86_64"]]}
          />
          <Values
            title="File"
            rows={[["Relative path", relativePath(record)], ["Format", "XML property list"], ["Source", "Invented sample record"]]}
          />
        </>
      )}
    </>
  );
}
