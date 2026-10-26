import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ConfirmProvider } from "../components/confirm";
import { useUnsavedGuard } from "../lib/useUnsavedGuard";

vi.mock("../auth", () => ({
  useAuth: () => ({ user: { id: 1, role: "admin" } }),
  canEdit: () => true,
  isAdmin: () => true,
}));
vi.mock("../api/client", () => ({
  api: { get: vi.fn(async (path: string) => path === "/datasets/7" ? {
    id: 7, table_name: "orders", schema_name: "public", connection_name: "warehouse",
    row_count: 100, active_checks: 0, open_exceptions: 0, health: "unknown", monitoring: "unknown",
  } : null) },
  ApiError: class extends Error {},
}));
vi.mock("./dataset/ProfileTab", () => ({ default: function DirtyProfile() {
  useUnsavedGuard(true);
  return <div>Unsaved profile editor</div>;
} }));
import DatasetDetailPage from "./DatasetDetailPage";

describe("guarded dataset disclosure navigation", () => {
  it("restores More focus after cancelling a technical-section navigation", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/datasets/7/profile"]}>
      <ConfirmProvider><Routes><Route path="/datasets/:id/:tab?" element={<DatasetDetailPage />} /></Routes></ConfirmProvider>
    </MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText("Unsaved profile editor")).toBeInTheDocument();
    const more = screen.getByRole("button", { name: "More" });
    await user.click(more);
    await user.tab();
    expect(screen.getByRole("link", { name: "Code" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(more).toHaveFocus();
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Unsaved profile editor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    await user.keyboard("{Enter}");
    expect(more).toHaveAttribute("aria-expanded", "true");
  });
});
