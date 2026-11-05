// Invented sample records. Later slices replace these with the repository API.

export type Grouping = "category" | "type" | "directory";

export interface PackageRecord {
  name: string;
  displayName: string;
  version: string;
  category: string;
  type: "Application" | "Package";
  directory: string;
  description: string;
  testing: boolean;
  production: boolean;
  minimumOs: string;
}

export const EDITABLE_FIELDS = [
  "displayName",
  "description",
  "testing",
  "production",
] as const;
export type EditableField = (typeof EDITABLE_FIELDS)[number];

function record(
  name: string,
  version: string,
  category: string,
  type: PackageRecord["type"],
  directory: string,
  description: string,
  production: boolean,
  minimumOs: string,
): PackageRecord {
  return {
    name,
    displayName: name,
    version,
    category,
    type,
    directory,
    description,
    testing: true,
    production,
    minimumOs,
  };
}

export const SAMPLE_PACKAGES: readonly PackageRecord[] = [
  record(
    "Firefox",
    "136.0",
    "Browsers",
    "Application",
    "apps/browsers",
    "A fast, independent web browser.",
    true,
    "10.15",
  ),
  record(
    "Chromium",
    "134.0",
    "Browsers",
    "Application",
    "apps/browsers",
    "An open source browser project.",
    false,
    "11.0",
  ),
  record(
    "Blender",
    "4.3.2",
    "Creative",
    "Application",
    "apps/creative",
    "Tools for 3D creation and animation.",
    false,
    "11.0",
  ),
  record(
    "Inkscape",
    "1.4",
    "Creative",
    "Application",
    "apps/creative",
    "A vector graphics editor.",
    true,
    "11.0",
  ),
  record(
    "IINA",
    "1.4.0",
    "Media",
    "Application",
    "apps/media",
    "The modern video player for macOS.",
    false,
    "10.15",
  ),
  record(
    "VLC",
    "3.0.21",
    "Media",
    "Application",
    "apps/media",
    "An open source media player.",
    true,
    "10.13",
  ),
  record(
    "Git",
    "2.48.1",
    "Utilities",
    "Package",
    "tools",
    "Distributed version control tools.",
    true,
    "11.0",
  ),
  record(
    "Keka",
    "1.4.8",
    "Utilities",
    "Application",
    "apps/utilities",
    "A file archiver for macOS.",
    false,
    "10.15",
  ),
];

export function relativePath(r: PackageRecord): string {
  return `pkgsinfo/${r.directory}/${r.name.toLowerCase()}-${r.version}.plist`;
}

export function installerItem(r: PackageRecord): string {
  return `${r.directory}/${r.name}-${r.version}${r.type === "Package" ? ".pkg" : ".dmg"}`;
}

export function changedFields(
  original: PackageRecord,
  current: PackageRecord,
): EditableField[] {
  return EDITABLE_FIELDS.filter((field) => original[field] !== current[field]);
}
