import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

let jobs: Record<string, unknown>[];
let posted: unknown[];

const catalogs = [
  { name: "all", path: "catalogs/all", items: 2, expected: 2, up_to_date: true, error: null },
  { name: "production", path: "catalogs/production", items: 1, expected: 2, up_to_date: false, error: null },
];

async function respond(request: Request): Promise<Response> {
  const { pathname } = new URL(request.url);
  const json = (v: unknown, status = 200) => Response.json(v, { status });
  if (pathname === "/api/session") return json({ name: "Local administrator", email: null, role: "Administrator", csrf_token: "t", sign_in: "none" });
  if (pathname === "/api/repositories") return json([{ id: "main", name: "Main", writable: true, live: false, last_scan: null }]);
  if (pathname === "/api/operations")
    return json([{ name: "catalog.generate", title: "Generate catalogs", requirements: ["catalog.generate"], available: true, needs: null }]);
  if (pathname.endsWith("/catalogs")) return json(catalogs);
  if (pathname.endsWith("/catalogs/production"))
    return json({
      ...catalogs[1],
      members: [{ name: "ExampleBrowser", version: "136.0", pkginfo: "pkgsinfo/ExampleBrowser-136.0.plist" }],
      missing: [{ name: "ExampleEditor", version: "2.10", pkginfo: "pkgsinfo/ExampleEditor-2.10.plist" }],
      extra: [],
    });
  if (pathname.endsWith("/catalogs/all")) return json({ ...catalogs[0], members: [], missing: [], extra: [] });
  if (pathname.endsWith("/jobs") && request.method === "POST") {
    posted.push(await request.json());
    const job = { id: "g1", operation: "catalog.generate", status: "queued", updated_at: "2026-11-05T10:00:00Z", error: null };
    jobs = [job];
    return json(job, 201);
  }
  if (pathname.endsWith("/jobs")) return json(jobs);
  if (pathname.endsWith("/packages")) return json({ items: [], total: 0, limit: 500, offset: 0 });
  if (pathname.endsWith("/objects")) return json({ items: [], total: 0, limit: 500, offset: 0 });
  return json(null, 404);
}

beforeEach(() => {
  jobs = [];
  posted = [];
  window.history.replaceState(null, "", "/?area=catalogs&catalog=production");
  vi.stubGlobal("fetch", vi.fn(respond));
});
afterEach(() => vi.unstubAllGlobals());

describe("catalogs", () => {
  it("shows what is out of date and why", async () => {
    render(<App />);
    expect(await screen.findByText("1 catalog is out of date")).toBeInTheDocument();
    const outOfDate = await screen.findByRole("region", { name: "Out of date" });
    expect(within(outOfDate).getByRole("button", { name: "ExampleEditor" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Members (1)" })).toBeInTheDocument();
  });

  it("starts a generation job and shows that it is running", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate catalogs" }));
    expect(await screen.findByRole("button", { name: "Generating…" })).toBeDisabled();
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ operation: "catalog.generate", arguments: {} });
  });
});
