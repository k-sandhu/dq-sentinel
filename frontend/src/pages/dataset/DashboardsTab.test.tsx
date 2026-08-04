import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { confirmMock, canEditMock } = vi.hoisted(() => ({
  confirmMock: vi.fn<(opts: { title: string }) => Promise<boolean>>(),
  canEditMock: vi.fn<() => boolean>(),
}));

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), del: vi.fn() },
  ApiError: class ApiError extends Error {
    status = 500;
  },
}));
vi.mock("../../auth", () => ({
  useAuth: () => ({ user: { id: 1, email: "a@b.c", role: "admin", is_active: true } }),
  canEdit: () => canEditMock(),
  isAdmin: () => true,
}));
vi.mock("../../components/confirm", () => ({ useConfirm: () => confirmMock }));
vi.mock("../../components/PanelChart", () => ({ default: () => <div>chart</div> }));

import { api } from "../../api/client";
import type { AdhocDashboard, AdhocDashboardMeta } from "../../api/types";
import DashboardsTab from "./DashboardsTab";

const mockGet = vi.mocked(api.get);
const mockPost = vi.mocked(api.post);

const meta = (id: number, o: Partial<AdhocDashboardMeta> = {}): AdhocDashboardMeta => ({
  id,
  dataset_id: 28,
  title: "trips overview",
  focus: "",
  origin: "heuristic",
  created_at: "2026-07-03T20:59:00Z",
  last_refreshed_at: null,
  panel_count: 5,
  ...o,
});

const board = (id: number, o: Partial<AdhocDashboardMeta> = {}): AdhocDashboard => ({
  ...meta(id, o),
  panels: [
    {
      title: "rows by day",
      description: "",
      sql: "SELECT 1",
      columns: ["d", "n"],
      rows: [["2026-07-01", 3]],
      viz: { type: "bar", x: "d", y: "n" },
      elapsed_ms: 4,
      error: null,
    },
  ],
});

function renderTab(boards: AdhocDashboardMeta[]) {
  mockGet.mockImplementation((path: string) => {
    if (path === "/health") return Promise.resolve({ llm_enabled: false } as never);
    if (path.startsWith("/adhoc-dashboards?")) return Promise.resolve(boards as never);
    const m = path.match(/^\/adhoc-dashboards\/(\d+)$/);
    if (m) return Promise.resolve(board(Number(m[1])) as never);
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <DashboardsTab datasetId={28} hasProfile />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const generateButton = () => screen.getByRole("button", { name: /Generate/ });

beforeEach(() => {
  canEditMock.mockReturnValue(true);
  confirmMock.mockResolvedValue(false);
});
afterEach(() => vi.clearAllMocks());

// #259: the tab listed saved boards but selected none, so the pane read "Pick or
// generate a dashboard" — which is what pushed analysts into pressing Generate
// again and ending up with two identical `trips overview`s.
describe("DashboardsTab", () => {
  it("opens the most recent board on load, without a click", async () => {
    renderTab([meta(9), meta(4, { title: "older overview" })]);
    expect(await screen.findByText("chart")).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("/adhoc-dashboards/9");
    expect(mockGet).not.toHaveBeenCalledWith("/adhoc-dashboards/4");
    expect(screen.queryByText("Pick or generate a dashboard")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /trips overview/ })[0]).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("does not auto-open a board for a viewer, who would only get a 403", async () => {
    canEditMock.mockReturnValue(false);
    renderTab([meta(9)]);
    expect(await screen.findByText("Dashboards are listed, not runnable here")).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalledWith("/adhoc-dashboards/9");
  });

  it("asks before generating a second board for the same focus", async () => {
    renderTab([meta(9)]);
    await screen.findByText("chart");
    fireEvent.click(generateButton());
    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(1));
    expect(mockPost).not.toHaveBeenCalled();
    expect(confirmMock.mock.calls[0][0]).toMatchObject({ title: "Generate a second dashboard?" });
  });

  it("generates the duplicate when the analyst confirms", async () => {
    confirmMock.mockResolvedValue(true);
    mockPost.mockResolvedValue(board(10) as never);
    renderTab([meta(9)]);
    await screen.findByText("chart");
    fireEvent.click(generateButton());
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/adhoc-dashboards/generate", {
        dataset_id: 28,
        focus: "",
      }),
    );
  });

  it("does not ask when the dataset has no board yet", async () => {
    mockPost.mockResolvedValue(board(1) as never);
    renderTab([]);
    await screen.findByText("Pick or generate a dashboard");
    fireEvent.click(generateButton());
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(confirmMock).not.toHaveBeenCalled();
  });

  it("does not ask when the focus differs from every saved board", async () => {
    mockPost.mockResolvedValue(board(11, { focus: "late trips" }) as never);
    renderTab([meta(9)]);
    await screen.findByText("chart");
    fireEvent.change(screen.getByLabelText("Dashboard focus (optional)"), {
      target: { value: "late trips" },
    });
    fireEvent.click(generateButton());
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(confirmMock).not.toHaveBeenCalled();
  });
});
