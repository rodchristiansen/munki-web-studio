import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("workspace shell", () => {
  it("renders the package list and selects a package", async () => {
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Work areas" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Blender/ }));
    expect(screen.getByRole("heading", { level: 2, name: "Blender" })).toBeInTheDocument();
  });

  it("filters packages", async () => {
    render(<App />);
    await userEvent.type(screen.getByRole("searchbox", { name: "Filter packages" }), "vlc");
    expect(screen.getByRole("button", { name: /VLC/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Firefox/ })).not.toBeInTheDocument();
  });

  it("moves between editor tabs with the arrow keys", async () => {
    render(<App />);
    const overview = screen.getByRole("tab", { name: "Overview" });
    overview.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Detection" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Detection" })).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Advanced" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Home}");
    expect(overview).toHaveAttribute("aria-selected", "true");
  });

  it("counts a draft change", async () => {
    render(<App />);
    const name = screen.getByRole("textbox", { name: "Display name" });
    await userEvent.clear(name);
    await userEvent.type(name, "Firefox ESR");
    expect(screen.getByText("1 draft change")).toBeInTheDocument();
  });
});
