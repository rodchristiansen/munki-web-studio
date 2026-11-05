import { useState } from "react";
import type { Grouping, PackageRecord } from "../sample";
import { Icon } from "./Icon";

const GROUPINGS: { value: Grouping; label: string }[] = [
  { value: "category", label: "Categories" },
  { value: "type", label: "Types" },
  { value: "directory", label: "Directories" },
];

interface Props {
  packages: readonly PackageRecord[];
  selected: number;
  onSelect: (index: number) => void;
}

export function PackageList({ packages, selected, onSelect }: Props) {
  const [grouping, setGrouping] = useState<Grouping>("category");
  const [query, setQuery] = useState("");

  const groups = new Map<string, { record: PackageRecord; index: number }[]>();
  packages.forEach((record, index) => {
    const haystack = [record.name, record.version, record.category, record.directory].join(" ");
    if (!haystack.toLowerCase().includes(query.toLowerCase())) return;
    const key = record[grouping];
    groups.set(key, [...(groups.get(key) ?? []), { record, index }]);
  });

  return (
    <aside className="object-list" aria-label="Package list">
      <div className="list-tools">
        <div className="segments" role="group" aria-label="Group packages">
          {GROUPINGS.map((g) => (
            <button
              key={g.value}
              type="button"
              aria-pressed={grouping === g.value}
              onClick={() => setGrouping(g.value)}
            >
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
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      {groups.size === 0 && <p className="empty-search">No matching packages</p>}
      {[...groups].map(([name, items]) => (
        <div key={name}>
          <div className="group">
            <Icon name={grouping === "directory" ? "folder" : "grid"} />
            {name}
            <small>{items.length}</small>
          </div>
          {items.map(({ record, index }) => (
            <button
              key={index}
              type="button"
              className="package"
              aria-pressed={index === selected}
              onClick={() => onSelect(index)}
            >
              <Icon name="package" />
              {record.name}
              <span>{record.version}</span>
            </button>
          ))}
        </div>
      ))}
    </aside>
  );
}
