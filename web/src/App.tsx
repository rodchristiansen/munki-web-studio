import { useState } from "react";
import icon from "./assets/munkistudio.png";
import { Icon } from "./components/Icon";
import { ManifestView } from "./components/ManifestView";
import { PackageEditor } from "./components/PackageEditor";
import { PackageList } from "./components/PackageList";
import { ReviewDialog } from "./components/ReviewDialog";
import { type PackageRecord, SAMPLE_PACKAGES, changedFields } from "./sample";

type Area = "packages" | "manifests";

export function App() {
  const [area, setArea] = useState<Area>("packages");
  const [drafts, setDrafts] = useState<PackageRecord[]>(() => SAMPLE_PACKAGES.map((r) => ({ ...r })));
  const [selected, setSelected] = useState(0);
  const [reviewing, setReviewing] = useState(false);

  const dirty = drafts.filter((d, i) => {
    const original = SAMPLE_PACKAGES[i];
    return original !== undefined && changedFields(original, d).length > 0;
  }).length;
  const current = drafts[selected];

  function update(record: PackageRecord) {
    setDrafts((all) => all.map((r, i) => (i === selected ? record : r)));
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="repo">
          <img className="mark" src={icon} alt="MunkiStudio app icon" width={36} height={36} />
          <div>
            <strong>Example repository</strong>
            <div>
              <small>Authoring workspace</small>
            </div>
          </div>
        </div>
        <div className="workspace-title">
          <strong>{area === "packages" ? "Packages" : "Manifests"}</strong>
          <small>{area === "packages" ? `${drafts.length} pkginfo files` : "1 sample manifest"}</small>
        </div>
        <div className="top-actions">
          <span className="branch">
            <Icon name="branch" /> main
          </span>
          <span className="pill">
            {dirty ? `${dirty} draft ${dirty === 1 ? "change" : "changes"}` : "No draft changes"}
          </span>
          <button type="button" className="toolbar-review" onClick={() => setReviewing(true)}>
            <Icon name="review" />
            Review draft
          </button>
        </div>
      </header>
      <div className="layout">
        <nav className="rail" aria-label="Work areas">
          <button type="button" aria-current={area === "packages" ? "page" : undefined} onClick={() => setArea("packages")}>
            <Icon name="package" />
            Packages
          </button>
          <button type="button" aria-current={area === "manifests" ? "page" : undefined} onClick={() => setArea("manifests")}>
            <Icon name="manifest" />
            Manifests
          </button>
          <div className="rail-label">Review</div>
          <button type="button" onClick={() => setReviewing(true)}>
            <Icon name="branch" />
            Changes <span id="change-count">{dirty}</span>
          </button>
          <div className="rail-footer">
            <span className="connection-dot" /> Sample repository
            <br />
            <small>No live connection</small>
          </div>
        </nav>
        <div className="workspace">
          {area === "packages" && current ? (
            <section aria-label="Package workspace">
              <div className="content">
                <PackageList packages={drafts} selected={selected} onSelect={setSelected} />
                <PackageEditor record={current} onChange={update} />
              </div>
            </section>
          ) : (
            <ManifestView />
          )}
        </div>
      </div>
      <footer className="statusbar">
        <span role="status">
          {dirty ? "Draft updated in this browser. No files have been written." : "Sample workspace ready. No repository connected."}
        </span>
        <span>Changes stay in memory · reload to reset</span>
      </footer>
      <ReviewDialog
        open={reviewing}
        originals={SAMPLE_PACKAGES}
        drafts={drafts}
        onClose={() => setReviewing(false)}
        onDiscard={() => {
          setDrafts(SAMPLE_PACKAGES.map((r) => ({ ...r })));
          setReviewing(false);
        }}
      />
    </div>
  );
}
