import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

type Job = Record<string, unknown> & { id: string; status: string };
let jobs: Job[];
let posts: { path: string; body: unknown }[];

function job(id: string, status: string, extra: Record<string, unknown> = {}): Job {
  return {
    id, repo_id: "main", operation: "repository.validate", title: "Validate the repository", arguments: {},
    input_revision: "a".repeat(64), status, attempts: 1, max_attempts: 3, cancel_requested: false,
    progress: status === "succeeded" ? 1 : 0.4, message: status === "running" ? "Checking references" : "",
    result: status === "succeeded" ? { errors: 2, warnings: 1 } : null, result_hash: null, error: null,
    created_by_name: "Local administrator", created_at: "2026-11-05T10:00:00Z", updated_at: "2026-11-05T10:00:00Z",
    finished_at: null, events: [], ...extra,
  };
}

async function respond(request: Request): Promise<Response> {
  const { pathname } = new URL(request.url);
  const json = (v: unknown, status = 200) => Response.json(v, { status });
  if (request.method === "POST") posts.push({ path: pathname, body: await request.json().catch(() => null) });
  if (pathname === "/api/session") return json({ name: "Local administrator", email: null, role: "Administrator", csrf_token: "t", sign_in: "none" });
  if (pathname === "/api/repositories") return json([{ id: "main", name: "Main", writable: false, live: false, last_scan: null }]);
  if (pathname === "/api/operations")
    return json([
      { name: "repository.validate", title: "Validate the repository", requirements: ["portable"], available: true },
      { name: "catalog.generate", title: "Generate catalogs", requirements: ["macos"], available: false },
    ]);
  if (pathname.endsWith("/jobs") && request.method === "POST") {
    const created = job("j2", "queued");
    jobs = [created, ...jobs];
    return json(created, 201);
  }
  if (pathname.endsWith("/jobs")) return json(jobs);
  if (pathname.endsWith("/cancel")) {
    jobs = jobs.map((j) => (j.id === "j1" ? { ...j, cancel_requested: true } : j));
    return json(jobs.find((j) => j.id === "j1"));
  }
  const id = pathname.split("/jobs/")[1];
  if (id) {
    const found = jobs.find((j) => j.id === id);
    return json({ ...found, events: [{ id: 1, at: "2026-11-05T10:00:00Z", kind: "queued", message: "Queued by Local administrator", progress: null }] });
  }
  if (pathname.endsWith("/packages")) return json({ items: [], total: 0, limit: 500, offset: 0 });
  if (pathname.endsWith("/objects")) return json({ items: [], total: 0, limit: 500, offset: 0 });
  return json(null, 404);
}

beforeEach(() => {
  jobs = [job("j1", "running")];
  posts = [];
  window.history.replaceState(null, "", "/?area=activity");
  vi.stubGlobal("fetch", vi.fn(respond));
});
afterEach(() => vi.unstubAllGlobals());

describe("activity", () => {
  it("lists jobs and shows the running one with its progress", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { level: 2, name: "Validate the repository" })).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /progress/ })).toHaveAttribute("value", "0.4");
    expect(await screen.findByText("Queued by Local administrator")).toBeInTheDocument();
  });

  it("starts a job with a fresh idempotency key and selects it", async () => {
    render(<App />);
    const list = await screen.findByRole("complementary", { name: "Jobs" });
    expect(within(list).getByRole("button", { name: "Generate catalogs" })).toBeDisabled();
    await userEvent.click(within(list).getByRole("button", { name: "Validate the repository" }));
    await waitFor(() => expect(window.location.search).toContain("job=j2"));
    const body = posts.find((p) => p.path.endsWith("/jobs"))?.body as { operation: string; idempotency_key: string };
    expect(body.operation).toBe("repository.validate");
    expect(body.idempotency_key.length).toBeGreaterThan(8);
  });

  it("asks a running job to cancel", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel job" }));
    expect(await screen.findByRole("button", { name: "Cancelling…" })).toBeDisabled();
    expect(posts.some((p) => p.path.endsWith("/jobs/j1/cancel"))).toBe(true);
  });
});
