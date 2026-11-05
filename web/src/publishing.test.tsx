import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

let live: boolean;
let posted: unknown[];

async function respond(request: Request): Promise<Response> {
  const { pathname } = new URL(request.url);
  const json = (v: unknown, status = 200) => Response.json(v, { status });
  if (pathname === "/api/session") return json({ name: "Local administrator", email: null, role: "Administrator", csrf_token: "t", sign_in: "none" });
  if (pathname === "/api/repositories") return json([{ id: "main", name: "Main", writable: true, live, last_scan: null }]);
  if (pathname.endsWith("/publishing"))
    return json(
      live
        ? { mode: "live", target: null, current: null, releases: [], unpublished_changes: false }
        : {
            mode: "snapshot",
            target: "/srv/published",
            current: { id: "abcdef1234567890abcd", published_at: "2026-11-05T10:00:00Z", repository_revision: "r", files: 12, bytes: 9000 },
            releases: [],
            unpublished_changes: true,
          },
    );
  if (pathname.endsWith("/git")) return json({ enabled: false, reason: "not a Git repository", branch: null, head: null, upstream: null, ahead: 0, behind: 0, changed: [] });
  if (pathname.endsWith("/jobs") && request.method === "POST") {
    posted.push(await request.json());
    return json({ id: "p1" }, 201);
  }
  if (pathname === "/api/workers" || pathname === "/api/operations") return json([]);
  if (pathname.endsWith("/packages")) return json({ items: [], total: 0, limit: 500, offset: 0 });
  if (pathname.endsWith("/objects")) return json({ items: [], total: 0, limit: 500, offset: 0 });
  return json(null, 404);
}

beforeEach(() => {
  posted = [];
  window.history.replaceState(null, "", "/?area=settings");
  vi.stubGlobal("fetch", vi.fn(respond));
});
afterEach(() => vi.unstubAllGlobals());

describe("publishing", () => {
  it("labels a live repository honestly and offers no publish step", async () => {
    live = true;
    render(<App />);
    expect(await screen.findByText(/Live on save: clients read this repository directly/)).toBeInTheDocument();
    expect(screen.getByText("· Live on save")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish to clients" })).not.toBeInTheDocument();
  });

  it("shows the current release and publishes as a job", async () => {
    live = false;
    render(<App />);
    expect(await screen.findByText("Changes not yet published")).toBeInTheDocument();
    expect(screen.getByText(/abcdef123456/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Publish to clients" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ operation: "repository.publish" });
    expect(await screen.findByText(/follow it in Activity/)).toBeInTheDocument();
  });
});
