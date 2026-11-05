import type { ObjectSummary, Package } from "../api/client";
import type { WorkspaceState } from "../url";
import { Icon } from "./Icon";

const GROUPINGS: { value: WorkspaceState["group"]; label: string }[] = [
  { value: "category", label: "Categories" },
  { value: "type", label: "Types" },
  { value: "directory", label: "Directories" },
];

interface Props {
  packages: Package[];
  invalid: ObjectSummary[];
  group: WorkspaceState["group"];
  query: string;
  selectedPath: string | null;
  onGroup: (group: WorkspaceState["group"]) => void;
  onQuery: (query: string) => void;
  onSelect: (path: string) => void;
}

function newest(pkg: Package) {
  return pkg.versions[pkg.versions.length - 1];
}

function groupKey(pkg: Package, group: WorkspaceState["group"]): string {
  const latest = newest(pkg);
  if (group === "type") return latest?.installer_type ?? "Unknown type";
  if (group === "directory") {
    const parts = (latest?.path ?? "").split("/").slice(1, -1);
    return parts.length ? parts.join("/") : "pkgsinfo";
  }
  return pkg.category ?? "Uncategorized";
}

export function PackageList(props: Props) {
  const { packages, invalid, group, query, selectedPath } = props;
  const groups = new Map<string, Package[]>();
  for (const pkg of packages) {
    const key = groupKey(pkg, group);
    groups.set(key, [...(groups.get(key) ?? []), pkg]);
  }
  const sorted = [...groups].sort(([a], [b]) => a.localeCompare(b));

  return (
    <aside className="object-list" aria-label="Package list">
      <div className="list-tools">
        <div className="segments" role="group" aria-label="Group packages">
          {GROUPINGS.map((g) => (
            <button key={g.value} type="button" aria-pressed={group === g.value} onClick={() => props.onGroup(g.value)}>
              {g.label}
            </button>
          ))}
        </div>
        <label className="search">
          <Icon name="search" />
          <input
            type="search"
            aria-label="Filter packages"
            placeholder="Filter packages"
            value={query}
            onChange={(e) => props.onQuery(e.target.value)}
          />
        </label>
      </div>
      {packages.length === 0 && invalid.length === 0 && <p className="empty-search">No matching packages</p>}
      {sorted.map(([name, items]) => (
        <div key={name}>
          <div className="group">
            <Icon name={group === "directory" ? "folder" : "grid"} />
            {name}
            <small>{items.length}</small>
          </div>
          {items.map((pkg) => {
            const latest = newest(pkg);
            const selected = pkg.versions.some((v) => v.path === selectedPath);
            return (
              <button
                key={pkg.name}
                type="button"
                className="package"
                aria-pressed={selected}
                onClick={() => latest && props.onSelect(latest.path)}
              >
                <Icon name="package" />
                {pkg.name}
                <span>{pkg.versions.length > 1 ? `${pkg.versions.length} versions` : latest?.version}</span>
              </button>
            );
          })}
        </div>
      ))}
      {invalid.length > 0 && (
        <div>
          <div className="group">
            <Icon name="folder" />
            Invalid files
            <small>{invalid.length}</small>
          </div>
          {invalid.map((obj) => (
            <button
              key={obj.path}
              type="button"
              className="package invalid"
              aria-pressed={obj.path === selectedPath}
              onClick={() => props.onSelect(obj.path)}
            >
              <Icon name="package" />
              {obj.path.split("/").pop()}
              <span>Invalid</span>
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}
