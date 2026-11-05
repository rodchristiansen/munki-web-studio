import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const repo = { id: "main", name: "Main repository", writable: false, last_scan: null };
const packages = [
  {
    name: "ExampleBrowser",
    display_name: "Example Browser",
    category: "Browsers",
    versions: [
      { version: "135.0", path: "pkgsinfo/apps/ExampleBrowser-135.0.plist", catalogs: ["production"], status: "ok", installer_type: "copy_from_dmg" },
      { version: "136.0", path: "pkgsinfo/apps/ExampleBrowser-136.0.plist", catalogs: ["testing"], status: "ok", installer_type: "copy_from_dmg" },
    ],
  },
  {
    name: "ExampleRuntime",
    display_name: null,
    category: "Utilities",
    versions: [{ version: "5.0", path: "pkgsinfo/tools/ExampleRuntime-5.0.plist", catalogs: ["testing"], status: "ok", installer_type: "pkg" }],
  },
];

function detail(path: string, name: string, version: string) {
  return {
    path, kind: "pkginfo", format: "xml-plist", status: "ok", revision: "abc123def4567890",
    name, version, display_name: null, category: null, catalogs: ["testing"], diagnostic: null,
    data: { $type: "dict", value: [["name", name], ["version", version], ["catalogs", ["testing"]], ["x_site", "kept"]] },
  };
}

function respond(url: string): unknown {
  const { pathname, searchParams } = new URL(url);
  if (pathname === "/api/repositories") return [repo];
  if (pathname.endsWith("/packages")) {
    const q = (searchParams.get("q") ?? "").toLowerCase();
    const items = packages.filter((p) => p.name.toLowerCase().includes(q));
    return { items, total: items.length, limit: 500, offset: 0 };
  }
  if (pathname.endsWith("/objects")) return { items: [], total: 0, limit: 500, offset: 0 };
  const path = decodeURIComponent(pathname.split("/objects/")[1] ?? "");
  for (const p of packages) for (const v of p.versions) if (v.path === path) return detail(path, p.name, v.version);
  return null;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => Response.json(respond(request.url))));
});
afterEach(() => vi.unstubAllGlobals());

describe("packages workspace", () => {
  it("lists packages from the API and opens the first one", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { level: 2, name: "ExampleBrowser" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ExampleRuntime/ })).toBeInTheDocument();
    expect(screen.getByText("x_site")).toBeInTheDocument();
  });

  it("keeps the selection in the URL and restores it on back", async () => {
    render(<App />);
    await screen.findByRole("heading", { level: 2, name: "ExampleBrowser" });
    await userEvent.click(screen.getByRole("button", { name: /ExampleRuntime/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "ExampleRuntime" })).toBeInTheDocument();
    expect(window.location.search).toContain("path=pkgsinfo%2Ftools%2FExampleRuntime-5.0.plist");
    act(() => {
      window.history.replaceState(null, "", "/?path=pkgsinfo%2Fapps%2FExampleBrowser-135.0.plist");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByRole("combobox", { name: "Version" })).toHaveValue("pkgsinfo/apps/ExampleBrowser-135.0.plist");
  });

  it("searches through the API", async () => {
    render(<App />);
    await screen.findByRole("heading", { level: 2, name: "ExampleBrowser" });
    await userEvent.type(screen.getByRole("searchbox", { name: "Filter packages" }), "runtime");
    await waitFor(() => expect(screen.queryByRole("button", { name: /ExampleBrowser/ })).not.toBeInTheDocument());
    expect(window.location.search).toContain("q=runtime");
  });

  it("moves between editor tabs with the arrow keys", async () => {
    render(<App />);
    const overview = await screen.findByRole("tab", { name: "Overview" });
    overview.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Detection" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Advanced" })).toHaveFocus();
  });
});
