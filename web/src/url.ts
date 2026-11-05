// Workspace state lives in the URL, so links, reloads, and back/forward all work.

export interface WorkspaceState {
  repo: string | null;
  area: "packages" | "manifests" | "catalogs" | "activity" | "settings";
  q: string;
  group: "category" | "type" | "directory";
  path: string | null;
  manifest: string | null;
  job: string | null;
  catalog: string | null;
}

const AREAS = ["packages", "manifests", "catalogs", "activity", "settings"] as const;

export function readState(search: string = window.location.search): WorkspaceState {
  const params = new URLSearchParams(search);
  const group = params.get("group");
  return {
    repo: params.get("repo"),
    area: AREAS.find((a) => a === params.get("area")) ?? "packages",
    q: params.get("q") ?? "",
    group: group === "type" || group === "directory" ? group : "category",
    path: params.get("path"),
    manifest: params.get("manifest"),
    job: params.get("job"),
    catalog: params.get("catalog"),
  };
}

export function toSearch(state: WorkspaceState): string {
  const params = new URLSearchParams();
  if (state.repo) params.set("repo", state.repo);
  if (state.area !== "packages") params.set("area", state.area);
  if (state.q) params.set("q", state.q);
  if (state.group !== "category") params.set("group", state.group);
  if (state.path) params.set("path", state.path);
  if (state.manifest) params.set("manifest", state.manifest);
  if (state.job) params.set("job", state.job);
  if (state.catalog) params.set("catalog", state.catalog);
  const text = params.toString();
  return text ? `?${text}` : window.location.pathname;
}
