import { Icon } from "./Icon";

export function ManifestView() {
  return (
    <section aria-label="Manifest workspace">
      <article className="manifest-content">
        <div className="object-title">
          <div className="app-icon" aria-hidden="true">
            <Icon name="manifest" />
          </div>
          <div>
            <h2>Shared / Design lab</h2>
            <div className="object-path">manifests/shared/design-lab.plist</div>
          </div>
        </div>
        <section className="editor-section">
          <h3>Catalog search order</h3>
          <ol>
            <li>Testing</li>
            <li>Production</li>
          </ol>
          <p className="muted">Order is preserved. Earlier catalogs take precedence.</p>
        </section>
        <section className="editor-section">
          <h3>Included manifests</h3>
          <ul className="manifest-list">
            <li>shared/base.plist <small>12 items</small></li>
            <li>shared/browsers.plist <small>3 items</small></li>
          </ul>
        </section>
        <section className="editor-section">
          <h3>Managed installs</h3>
          <ul className="manifest-list">
            <li>Firefox <small>All clients</small></li>
            <li>VLC <small>All clients</small></li>
            <li>Blender <span className="condition">Conditional assignment</span></li>
          </ul>
        </section>
        <p className="summary-note">Sample manifest. Repository data arrives with the read API.</p>
      </article>
    </section>
  );
}
