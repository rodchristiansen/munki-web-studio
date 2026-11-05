// Typed client for the studio API, generated from openapi.json.
// Regenerate the types after an API change: `uv run mwsctl openapi --output web/openapi.json`
// from the repository root, then `pnpm gen:api` here.
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

// fetch is looked up on each call so tests can replace it.
export const api = createClient<paths>({
  baseUrl: typeof window === "undefined" ? "" : window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

export type Package = components["schemas"]["Package"];
export type ObjectSummary = components["schemas"]["ObjectSummary"];
export type ObjectDetail = components["schemas"]["ObjectDetail"];
export type RepositoryInfo = components["schemas"]["RepositoryInfo"];
export type PackageVersion = components["schemas"]["PackageVersion"];
