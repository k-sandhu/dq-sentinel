import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), del: vi.fn() },
  ApiError: class ApiError extends Error {
    status = 500;
  },
}));
vi.mock("../auth", () => ({
  useAuth: () => ({ user: { id: 1, email: "a@b.c", role: "admin", is_active: true } }),
  canEdit: () => true,
  isAdmin: () => true,
}));
vi.mock("../components/confirm", () => ({ useConfirm: () => vi.fn(async () => false) }));
vi.mock("../lib/useUnsavedGuard", () => ({
  useUnsavedGuard: () => ({ bypass: (fn: () => void) => fn() }),
}));
vi.mock("./dataset/ProfileTab", () => ({ default: () => <div>profile tab</div> }));

import { api } from "../api/client";
import type { Dataset } from "../api/types";
import DatasetDetailPage from "./DatasetDetailPage";

const mockGet = vi.mocked(api.get);

const makeDataset = (o: Partial<Dataset>): Dataset =>
  ({
    id: 7,
    connection_id: 1,
    connection_name: "warehouse",
    schema_name: "public",
    table_name: "orders",
    display_name: "orders",
    row_count: 100,
    last_profiled_at: null,
    created_at: "2026-01-01T00:00:00Z",
    active_checks: 0,
    open_exceptions: 0,
    health: "fail",
    monitoring: "unknown",
    failing_checks: 0,
    errored_checks: 0,
    last_error: null,
    last_error_run_id: null,
    importance: null,
    owner: null,
    domain: null,
    team: null,
    slo_target_score: null,
    slo_window_days: null,
    slo_enabled: false,
    ...o,
  }) as Dataset;

function renderPage(dataset: Dataset) {
  mockGet.mockImplementation((path: string) => {
    if (path === "/datasets/7") return Promise.resolve(dataset as never);
    return Promise.reject(new Error("not found"));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/datasets/7"]}>
        <Routes>
          <Route path="/datasets/:id" element={<DatasetDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

// #262 (H-1): the list row says "checks broken / N not running" and links to the
// errored run. One click in, the header used to show a bare red `fail` with no
// reason — the two surfaces contradicting each other about the same dataset.
describe("DatasetDetailPage health header", () => {
  it("shows the repair affordance instead of a bare `fail` when the verdict is entirely errors", async () => {
    renderPage(
      makeDataset({
        health: "fail",
        active_checks: 16,
        failing_checks: 0,
        errored_checks: 16,
        monitoring: "broken",
        last_error: "authentication with the source failed",
        last_error_run_id: 42,
      }),
    );
    const pill = await screen.findByRole("link", { name: "checks broken" });
    expect(pill).toHaveAttribute("href", "/runs/42");
    expect(pill).toHaveAccessibleDescription(/16 of 16 active checks could not run/);
    expect(screen.queryByText("fail")).not.toBeInTheDocument();
  });

  it("keeps the verdict and adds the repair chip when checks both fail and error", async () => {
    renderPage(
      makeDataset({
        health: "fail",
        active_checks: 5,
        failing_checks: 2,
        errored_checks: 3,
        monitoring: "degraded",
        last_error_run_id: null,
      }),
    );
    expect(await screen.findByText("fail")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "3 not running" })).toHaveAttribute(
      "href",
      "/datasets/7/runs",
    );
  });

  it("renders the plain verdict when nothing is erroring", async () => {
    renderPage(makeDataset({ health: "warn", active_checks: 4, monitoring: "ok" }));
    expect(await screen.findByText("warn")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /not running|checks broken/ }),
    ).not.toBeInTheDocument();
  });
});
