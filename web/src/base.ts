// Where the studio lives: the site root when it runs alone, or a host's prefix
// (such as /munki) when FleetMate mounts it. The server writes it into <base>.

export function baseUrl(): string {
  if (typeof document === "undefined") return "";
  return new URL(".", document.baseURI).href.replace(/\/$/, "");
}

/** An absolute URL for a studio path such as "/api/session". */
export function studioUrl(path: string): string {
  return `${baseUrl()}${path}`;
}
