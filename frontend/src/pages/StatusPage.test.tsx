import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({ api: { get: vi.fn() } }));

import { api } from "../api/client";
import type { DataStatus } from "../api/types";
import StatusPage from "./StatusPage";

const mockGet = vi.mocked(api.get);

const EMPTY: DataStatus = {
  overall: "unknown",
  operational: 0,
  delayed: 0,
  degraded: 0,
  datasets: [],
  updates: [],
  generated_at: "2026-06-26T00:00:00Z",
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <StatusPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("StatusPage rendering states", () => {
  it("never claims 'operational' when the status fetch fails (#179 review)", async () => {
    mockGet.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    expect(await screen.findByText("status unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/operational/i)).toBeNull(); // no false operational claim
    expect(screen.queryByText("No monitored datasets")).toBeNull(); // error != empty
  });

  it("shows an honest empty state, not 'operational', when zero datasets are monitored", async () => {
    mockGet.mockResolvedValueOnce(EMPTY);
    renderPage();
    expect(await screen.findByText("No monitored datasets")).toBeInTheDocument();
    expect(screen.getByText("no monitored datasets")).toBeInTheDocument(); // the header pill
    expect(screen.queryByText(/all systems operational/i)).toBeNull();
  });

  it("renders dataset tiles + an operational headline when data is healthy", async () => {
    mockGet.mockResolvedValueOnce({
      ...EMPTY,
      overall: "operational",
      operational: 1,
      datasets: [
        { id: 1, name: "orders", health: "operational", open_incidents: 0, last_incident_at: null },
      ],
    });
    renderPage();
    expect(await screen.findByText("orders")).toBeInTheDocument();
    expect(screen.getByText("all systems operational")).toBeInTheDocument();
  });
});
