import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

// A small in-memory studio: one writable repository, one package, one manifest, and
// a change-set store that records what the page sends.
type Change = { op: string; path: string[]; value?: unknown };
type File = {
  path: string;
  base_revision: string;
  changes: Change[];
  conflict: boolean;
  current_revision: string | null;
  diff: string;
  error: string | null;
  surgical: boolean;
};

let files: File[];
let open: boolean;
let saveStatus: number;
let sent: { method: string; path: string; body: unknown }[];
let gitEnabled = false;

const PKG = "pkgsinfo/apps/ExampleBrowser-136.0.plist";
const MANIFEST = "manifests/site_default";

function changeSet() {
  return {
    id: "c1",
    repo_id: "main",
    title: "Edits",
    owner: "local",
    owner_name: "Local administrator",
    status: "open",
    created_at: "",
    updated_at: "",
    files,
  };
}

function upsert(path: string, base: string, changes: Change[]) {
  const others = files.filter((f) => f.path !== path);
  files = changes.length
    ? [
        ...others,
        {
          path,
          base_revision: base,
          changes,
          conflict: false,
          current_revision: base,
          diff: `--- a/${path}\n+++ b/${path}\n+edited`,
          error: null,
          surgical: true,
        },
      ]
    : others;
}

async function respond(request: Request): Promise<Response> {
  const { pathname } = new URL(request.url);
  const body =
    request.method === "GET" || request.method === "DELETE"
      ? undefined
      : await request.json().catch(() => undefined);
  if (request.method !== "GET")
    sent.push({ method: request.method, path: pathname, body });
  const json = (value: unknown, status = 200) =>
    Response.json(value, { status });

  if (pathname === "/api/session")
    return json({
      name: "Local administrator",
      email: null,
      role: "Administrator",
      csrf_token: "t",
      sign_in: "none",
    });
  if (pathname === "/api/repositories")
    return json([
      {
        id: "main",
        name: "Main",
        writable: true,
        live: false,
        last_scan: null,
      },
    ]);
  if (pathname.endsWith("/git"))
    return json(
      gitEnabled
        ? { enabled: true, reason: null, branch: "main", head: "abc123", upstream: null, ahead: 0, behind: 0, changed: [] }
        : { enabled: false, reason: "not a Git repository", branch: null, head: null, upstream: null, ahead: 0, behind: 0, changed: [] },
    );
  if (pathname.endsWith("/commit")) return json({ commit: "0123456789abcdef", paths: [PKG] });
  if (pathname === "/api/fields")
    return json({
      display_name: "string",
      description: "text",
      unattended_install: "boolean",
      catalogs: "string-list",
    });
  if (pathname.endsWith("/packages"))
    return json({
      items: [
        {
          name: "ExampleBrowser",
          display_name: null,
          category: null,
          versions: [
            {
              version: "136.0",
              path: PKG,
              catalogs: ["testing"],
              status: "ok",
              installer_type: null,
            },
          ],
        },
      ],
      total: 1,
      limit: 500,
      offset: 0,
    });
  if (pathname.endsWith("/objects")) {
    const kind = new URL(request.url).searchParams.get("kind");
    const items =
      kind === "manifest"
        ? [
            {
              path: MANIFEST,
              kind: "manifest",
              status: "ok",
              name: "site_default",
            },
          ]
        : [];
    return json({ items, total: items.length, limit: 500, offset: 0 });
  }
  if (pathname.includes("/objects/"))
    return json({
      path: PKG,
      kind: "pkginfo",
      format: "xml-plist",
      status: "ok",
      revision: "rev1",
      name: "ExampleBrowser",
      version: "136.0",
      display_name: null,
      category: null,
      catalogs: ["testing"],
      diagnostic: null,
      data: {
        $type: "dict",
        value: [
          ["name", "ExampleBrowser"],
          ["version", "136.0"],
          ["catalogs", ["testing"]],
        ],
      },
    });
  if (pathname.includes("/manifests/"))
    return json({
      path: MANIFEST,
      name: "site_default",
      status: "ok",
      revision: "mrev",
      diagnostic: null,
      catalogs: ["production"],
      includes: [],
      sections: [
        {
          field: "managed_installs",
          items: [
            { name: "ExampleBrowser", package: "ExampleBrowser" },
            { name: "ExampleRuntime", package: null },
          ],
        },
      ],
      conditional: [],
      included_by: [],
      issues: [],
    });

  if (pathname.endsWith("/changesets") && request.method === "GET")
    return json(open ? [changeSet()] : []);
  if (pathname.endsWith("/changesets") && request.method === "POST") {
    open = true;
    return json(changeSet());
  }
  const b = body as {
    base_revision: string;
    fields?: Record<string, unknown>;
    sections?: Record<string, string[]>;
    changes?: Change[];
  };
  if (pathname.includes("/pkginfo/")) {
    const existing = files.find((f) => f.path === PKG)?.changes ?? [];
    const next = Object.entries(b.fields ?? {}).map(([k, v]): Change =>
      v === null
        ? { op: "delete", path: [k] }
        : { op: "set", path: [k], value: v },
    );
    upsert(PKG, b.base_revision, [
      ...existing.filter((c) => !next.some((n) => n.path[0] === c.path[0])),
      ...next,
    ]);
    return json(changeSet());
  }
  if (pathname.includes("/manifest/")) {
    upsert(
      MANIFEST,
      b.base_revision,
      Object.entries(b.sections ?? {}).map(([k, v]) => ({
        op: "set",
        path: [k],
        value: v,
      })),
    );
    return json(changeSet());
  }
  if (pathname.includes("/files/")) {
    const path = decodeURIComponent(pathname.split("/files/")[1] ?? "");
    upsert(path, b.base_revision, b.changes ?? []);
    return json(changeSet());
  }
  if (pathname.endsWith("/save")) {
    if (saveStatus === 409) {
      files = files.map((f) => ({
        ...f,
        conflict: true,
        current_revision: "rev2",
      }));
      return json(
        {
          detail: {
            message:
              "pkgsinfo/apps/ExampleBrowser-136.0.plist changed since it was edited",
            paths: [PKG],
          },
        },
        409,
      );
    }
    const saved = changeSet();
    open = false;
    files = [];
    return json({ ...saved, status: "saved" });
  }
  if (request.method === "DELETE") {
    open = false;
    files = [];
    return json({ ...changeSet(), status: "discarded" });
  }
  if (pathname.endsWith("/rescan")) return json({});
  return json(null, 404);
}

beforeEach(() => {
  gitEnabled = false;
  files = [];
  open = false;
  saveStatus = 200;
  sent = [];
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", vi.fn(respond));
});
afterEach(() => vi.unstubAllGlobals());

describe("editing", () => {
  it("drafts a field edit and saves it after review", async () => {
    render(<App />);
    const field = await screen.findByRole("textbox", { name: "Display name" });
    await userEvent.type(field, "Example Browser");
    await userEvent.tab();
    await waitFor(() =>
      expect(sent.some((s) => s.path.includes("/pkginfo/"))).toBe(true),
    );
    const patch = sent.find((s) => s.path.includes("/pkginfo/"));
    expect(patch?.body).toEqual({
      base_revision: "rev1",
      fields: { display_name: "Example Browser" },
    });

    await userEvent.click(
      await screen.findByRole("button", { name: /Review draft · 1 file/ }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "1 file changed",
    });
    expect(within(dialog).getByText(/\+edited/)).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Save to repository" }),
    );
    expect(
      await screen.findByText("Saved 1 file to the repository."),
    ).toBeInTheDocument();
    expect(sent.find((s) => s.path.endsWith("/save"))?.body).toEqual({
      acknowledge_live: false,
    });
  });

  it("sends a boolean as true or false and clearing as removal", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("tab", { name: "Install" }));
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: /Unattended install/ }),
      "yes",
    );
    await waitFor(() =>
      expect(sent.at(-1)?.body).toEqual({
        base_revision: "rev1",
        fields: { unattended_install: true },
      }),
    );
  });

  it("shows a conflict and reapplies the edits to the current file", async () => {
    render(<App />);
    await userEvent.type(
      await screen.findByRole("textbox", { name: "Display name" }),
      "New",
    );
    await userEvent.tab();
    saveStatus = 409;
    await userEvent.click(
      await screen.findByRole("button", { name: /Review draft/ }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Save to repository" }),
    );
    expect(
      await within(dialog).findByText("Changed on disk"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Save to repository" }),
    ).toBeDisabled();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Reapply to current file" }),
    );
    await waitFor(() =>
      expect(
        within(dialog).queryByText("Changed on disk"),
      ).not.toBeInTheDocument(),
    );
    expect(sent.at(-1)?.body).toMatchObject({ base_revision: "rev2" });
  });

  it("reorders and adds manifest items by keyboard", async () => {
    window.history.replaceState(null, "", "/?area=manifests");
    render(<App />);
    const move = await screen.findByRole("button", {
      name: "Move ExampleRuntime up",
    });
    move.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() =>
      expect(sent.at(-1)?.body).toEqual({
        base_revision: "mrev",
        sections: { managed_installs: ["ExampleRuntime", "ExampleBrowser"] },
      }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Add to Catalogs" }),
      "testing{Enter}",
    );
    await waitFor(() =>
      expect(sent.at(-1)?.body).toEqual({
        base_revision: "mrev",
        sections: { catalogs: ["production", "testing"] },
      }),
    );
  });

  it("does not offer editing on a read-only repository", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (new URL(request.url).pathname === "/api/repositories")
          return Response.json([
            {
              id: "main",
              name: "Main",
              writable: false,
              live: false,
              last_scan: null,
            },
          ]);
        return respond(request);
      }),
    );
    render(<App />);
    expect(
      await screen.findByRole("textbox", { name: "Display name" }),
    ).toHaveAttribute("readonly");
    expect(screen.getAllByText("Read-only").length).toBeGreaterThan(0);
  });

  it("offers to commit exactly the saved files when the repository uses Git", async () => {
    gitEnabled = true;
    render(<App />);
    expect(await screen.findByText(/· main/)).toBeInTheDocument();
    await userEvent.type(await screen.findByRole("textbox", { name: "Display name" }), "Committed");
    await userEvent.tab();
    await userEvent.click(await screen.findByRole("button", { name: /Review draft/ }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Save to repository" }));
    const dialog = await screen.findByRole("dialog", { name: "Commit the saved files" });
    expect(within(dialog).getByText(PKG)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Commit" }));
    expect(await screen.findByText("Committed 01234567 on main.")).toBeInTheDocument();
    expect(sent.find((x) => x.path.endsWith("/commit"))?.body).toEqual({ message: "Edits" });
  });
});
