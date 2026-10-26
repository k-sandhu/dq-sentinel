import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({ api: { get: vi.fn(async () => []) } }));
vi.mock("../auth", () => ({ useAuth: () => ({ user: { email: "viewer@example.com", role: "viewer" }, logout: vi.fn() }) }));
vi.mock("./AppearanceDrawer", () => ({ AppearanceButton: () => <button>Appearance</button> }));
vi.mock("./DocsLauncher", () => ({ default: () => null }));
import Layout, { activeNavigationGroup } from "./Layout";

beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
});

function renderLayout(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}>
    <Routes><Route element={<Layout />}><Route path="*" element={<div>Page content</div>} /></Route></Routes>
  </MemoryRouter></QueryClientProvider>);
}

describe("task navigation", () => {
  it.each([
    ["/my-work", "Overview"], ["/catalog", "Datasets"], ["/connections/9/browse", "Datasets"],
    ["/datasets/7/contract", "Datasets"], ["/checks/8", "Checks"], ["/runs/9", "Checks"],
    ["/incidents", "Issues"], ["/reliability", "Reports"], ["/status", "Reports"],
    ["/dashboards/4", "Reports"], ["/lineage", "Explore"], ["/assistant", "Explore"],
  ])("groups direct route %s under %s", (path, label) => {
    expect(activeNavigationGroup(path)?.label).toBe(label);
  });

  it("shows six primary tasks and contextual links for the current task", () => {
    const { container } = renderLayout("/runs/9");
    const sidebar = within(container.querySelector("aside")!);
    const primary = sidebar.getByRole("navigation", { name: "Primary" });
    expect(within(primary).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Overview", "Datasets", "Checks", "Issues", "Reports", "Explore",
    ]);
    expect(within(primary).getByRole("link", { name: "Checks" })).toHaveAttribute("aria-current", "location");
    const context = screen.getByRole("navigation", { name: "Checks navigation" });
    expect(within(context).getByRole("link", { name: "Runs" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "Incidents" })).not.toBeInTheDocument();
    expect(sidebar.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("makes secondary destinations reachable from their primary task", async () => {
    const { container } = renderLayout("/");
    const primary = within(container.querySelector("aside")!).getByRole("navigation", { name: "Primary" });
    const user = userEvent.setup();
    await user.click(within(primary).getByRole("link", { name: "Explore" }));
    const context = screen.getByRole("navigation", { name: "Explore navigation" });
    const assistant = within(context).getByRole("link", { name: "Assistant" });
    assistant.focus();
    await user.keyboard("{Enter}");
    expect(assistant).toHaveAttribute("aria-current", "page");
    expect(within(primary).getByRole("link", { name: "Explore" })).toHaveClass("active");
  });
});
