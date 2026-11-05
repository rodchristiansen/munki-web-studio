import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

let received: number;
let dropNext: boolean;
let calls: { method: string; path: string; body?: unknown; offset?: string | null }[];
let drafted: boolean;
const SIZE = 20 * 1024 * 1024;

async function respond(request: Request): Promise<Response> {
  const { pathname } = new URL(request.url);
  const json = (v: unknown, status = 200) => Response.json(v, { status });
  const entry: (typeof calls)[number] = { method: request.method, path: pathname, offset: request.headers.get("Upload-Offset") };
  calls.push(entry);
  if (pathname === "/api/session") return json({ name: "Local administrator", email: null, role: "Administrator", csrf_token: "t", sign_in: "none" });
  if (pathname === "/api/repositories") return json([{ id: "main", name: "Main", writable: true, live: false, last_scan: null }]);
  if (pathname === "/api/operations")
    return json([{ name: "package.import", title: "Import a package", requirements: ["installer.inspect"], available: true, needs: null }]);
  if (pathname === "/api/fields") return json({});
  if (pathname.endsWith("/uploads") && request.method === "POST") {
    entry.body = await request.json();
    return json({ id: "u1", filename: "Example.pkg", size: SIZE, sha256: "a".repeat(64), received: 0, complete: false }, 201);
  }
  if (pathname.endsWith("/uploads/u1") && request.method === "PATCH") {
    const chunk = new Uint8Array(await request.arrayBuffer());
    if (dropNext) {
      dropNext = false;
      throw new TypeError("network down");
    }
    received += chunk.length;
    return json({ id: "u1", received, complete: received === SIZE });
  }
  if (pathname.endsWith("/uploads/u1")) return json({ id: "u1", filename: "Example.pkg", size: SIZE, sha256: "a".repeat(64), received, complete: received === SIZE });
  if (pathname.endsWith("/jobs") && request.method === "POST") {
    entry.body = await request.json();
    return json({ id: "j1", status: "queued" }, 201);
  }
  if (pathname.endsWith("/jobs/j1")) {
    drafted = true;
    return json({ id: "j1", status: "succeeded", result: { draft: { change_set: "c9" } }, error: null, events: [] });
  }
  if (pathname.endsWith("/changesets") && request.method === "GET")
    return json(
      drafted
        ? [
            {
              id: "c9", repo_id: "main", owner: "local", owner_name: "Local administrator", title: "Import Example 1.0", status: "open",
              created_at: "", updated_at: "",
              files: [{ path: "pkgsinfo/apps/Example-1.0.plist", base_revision: "", current_revision: null, changes: [], conflict: false, error: null, surgical: false, diff: "+<key>name</key>" }],
              payloads: [{ path: "pkgs/apps/Example.pkg", size: SIZE, sha256: "a".repeat(64), conflict: false }],
            },
          ]
        : [],
    );
  if (pathname.endsWith("/git")) return json({ enabled: false, reason: "not a Git repository", branch: null, head: null, upstream: null, ahead: 0, behind: 0, changed: [] });
  if (pathname.endsWith("/packages")) return json({ items: [], total: 0, limit: 500, offset: 0 });
  if (pathname.endsWith("/objects")) return json({ items: [], total: 0, limit: 500, offset: 0 });
  return json(null, 404);
}

beforeEach(() => {
  received = 0;
  dropNext = false;
  drafted = false;
  calls = [];
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", vi.fn(respond));
});
afterEach(() => vi.unstubAllGlobals());

describe("importing", () => {
  it("uploads in chunks, resumes after a dropped chunk, and opens the draft", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Import a package…" }));
    const dialog = await screen.findByRole("dialog", { name: "Import a package" });
    const file = new File([new Uint8Array(SIZE)], "Example.pkg", { type: "application/octet-stream" });
    await userEvent.upload(within(dialog).getByLabelText("Installer"), file);
    dropNext = true;
    await userEvent.click(within(dialog).getByRole("button", { name: "Upload and import" }));
    const review = await screen.findByRole("dialog", { name: "1 file changed" }, { timeout: 8000 });
    expect(within(review).getByText("pkgs/apps/Example.pkg")).toBeInTheDocument();
    expect(received).toBe(SIZE);
    const offsets = calls.filter((c) => c.method === "PATCH").map((c) => c.offset);
    expect(offsets[0]).toBe("0");
    expect(offsets.filter((o) => o === "0").length).toBe(2);
    const created = calls.find((c) => c.path.endsWith("/uploads") && c.method === "POST")?.body as { sha256: string; size: number };
    expect(created.size).toBe(SIZE);
    expect(created.sha256).toMatch(/^[0-9a-f]{64}$/);
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/jobs") && c.method === "POST")).toBe(true));
  }, 15000);
});
