// Regression tests for the triage workspace's selection lifecycle (#286) and the
// CSV-export failure path (#294).
//
// The interesting part of #286 is a *timing* bug, so these tests deliberately make
// the post-triage refetch resolve asynchronously: an implementation that prunes
// against the page the mutation started from still sees the rows and keeps the
// selection, which fails the assertions below.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/client";
import type { ExceptionPage, ExceptionRecord } from "../../api/types";
import ExceptionsWorkspace, { pruneTriagedSelection } from "./ExceptionsWorkspace";
import { exportErrorMessage } from "./FilterBar";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  download: vi.fn(),
}));

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    api: { ...actual.api, get: mocks.get, post: mocks.post, download: mocks.download },
  };
});

vi.mock("../../auth", async () => {
  const actual = await vi.importActual<typeof import("../../auth")>("../../auth");
  return {
    ...actual,
    useAuth: () => ({
      user: { id: 1, email: "ed@example.com", name: "Ed", role: "editor", is_active: true, created_at: "2026-01-01T00:00:00Z" },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

// ---------------------------------------------------------------------------
// pure logic
// ---------------------------------------------------------------------------

describe("pruneTriagedSelection", () => {
  it("drops triaged ids the refetched page no longer contains", () => {
    const { next, dropped } = pruneTriagedSelection(new Set([1, 2, 3]), [1, 2, 3], new Set([3]));
    expect([...next]).toEqual([3]);
    expect(dropped).toBe(2);
  });

  it("keeps triaged ids that are still visible (two-step ack -> assign)", () => {
    const { next, dropped } = pruneTriagedSelection(new Set([1, 2]), [1, 2], new Set([1, 2]));
    expect([...next]).toEqual([1, 2]);
    expect(dropped).toBe(0);
  });

  it("never touches ids this mutation did not triage", () => {
    // Single-row triage from the detail panel while a cross-page bulk selection
    // is held: ids 8/9 live on another page and must survive.
    const { next, dropped } = pruneTriagedSelection(new Set([8, 9, 4]), [4], new Set());
    expect([...next]).toEqual([8, 9]);
    expect(dropped).toBe(1);
  });
});

describe("exportErrorMessage", () => {
  it("tells a viewer why a 403 export failed and what to do", () => {
    const msg = exportErrorMessage(new ApiError(403, "Not enough permissions"));
    expect(msg).toMatch(/permission/i);
    expect(msg).toMatch(/admin/i);
  });

  it("labels server errors with the status and suggests a retry", () => {
    expect(exportErrorMessage(new ApiError(500, "boom"))).toMatch(/500/);
    expect(exportErrorMessage(new ApiError(500, "boom"))).toMatch(/retry/i);
  });

  it("falls back to a connectivity message for non-API failures", () => {
    expect(exportErrorMessage(new TypeError("Failed to fetch"))).toMatch(/connection/i);
  });
});

// ---------------------------------------------------------------------------
// workspace integration
// ---------------------------------------------------------------------------

const row = (id: number, status = "open"): ExceptionRecord =>
  ({
    id,
    run_id: 1,
    check_id: 1,
    check_name: "orders.total not null",
    check_type: "not_null",
    check_severity: "error",
    column_name: "total",
    dataset_id: 1,
    dataset_name: "orders",
    row_data: {},
    reason: `row ${id} violates not_null`,
    outlier_score: null,
    status,
    note: "",
    marked_by: null,
    marked_at: null,
    created_at: "2026-07-01T00:00:00Z",
    fingerprint: null,
    first_seen_at: "2026-07-01T00:00:00Z",
    last_seen_at: "2026-07-01T00:00:00Z",
    last_run_id: 1,
    occurrence_count: 1,
    version: 1,
    assigned_to_id: null,
    assigned_to: null,
  }) as ExceptionRecord;

const page = (items: ExceptionRecord[]): ExceptionPage => ({
  items,
  total: items.length,
  limit: 50,
  offset: 0,
});

/** Current server-side page; tests swap it when the triage POST lands. */
let currentPage: ExceptionPage = page([]);

function renderWorkspace(search: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/exceptions?${search}`]}>
        <ExceptionsWorkspace />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  currentPage = page([row(1), row(2)]);
  mocks.get.mockImplementation(async (path: string) => {
    if (path.startsWith("/exceptions/facets")) {
      return { status: {}, severity: {}, check_type: {}, datasets: [], total: currentPage.total };
    }
    if (path.startsWith("/exceptions/view-counts")) {
      return { my_open: 0, new_today: 0, high_severity: 0, recurring: 0, unassigned: 0, expected: 0 };
    }
    if (path.startsWith("/exceptions")) {
      // Non-instant so a prune that reads the cache too early sees the OLD page.
      await new Promise((r) => setTimeout(r, 5));
      return currentPage;
    }
    if (path.startsWith("/auth/assignees")) return [];
    if (path.startsWith("/checks/types")) return [];
    throw new Error(`unexpected GET ${path}`);
  });
});

async function selectRowsOneAndTwo(search: string) {
  renderWorkspace(search);
  await screen.findByLabelText("Select exception 1");
  fireEvent.click(screen.getByLabelText("Select exception 1"));
  fireEvent.click(screen.getByLabelText("Select exception 2"));
  expect(await screen.findByText("2 selected")).toBeInTheDocument();
}

describe("ExceptionsWorkspace selection after triage (#286)", () => {
  it("prunes the selection when the triaged rows leave the filtered view", async () => {
    await selectRowsOneAndTwo("status=open");
    mocks.post.mockImplementation(async (_path: string, body: { ids: number[] }) => {
      // Resolving inside a status=open queue empties the page.
      currentPage = page([]);
      return body.ids.map((id) => row(id, "resolved"));
    });

    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));

    // Bulk bar is gone: nothing is selected, so nothing can be mis-targeted.
    await waitFor(() => expect(screen.queryByText(/\d+ selected/)).not.toBeInTheDocument());
    // ...and the shrink is announced rather than silent.
    expect(await screen.findByRole("status")).toHaveTextContent(/2 triaged rows left this view/i);
  });

  it("keeps the selection when the triaged rows are still on the page (ack -> assign)", async () => {
    await selectRowsOneAndTwo(""); // no status filter: acknowledged rows stay
    mocks.post.mockImplementation(async (_path: string, body: { ids: number[] }) => {
      currentPage = page(body.ids.map((id) => row(id, "acknowledged")));
      return body.ids.map((id) => row(id, "acknowledged"));
    });

    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    await screen.findByText("acknowledged");

    // Selection survived, so the second step of the two-step flow still targets
    // the same rows.
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(mocks.post.mock.calls[1][1]).toMatchObject({ ids: [1, 2], status: "resolved" });
  });

  it("prunes only the rows that vanished, keeping the rest selected", async () => {
    await selectRowsOneAndTwo("status=open");
    mocks.post.mockImplementation(async (_path: string, body: { ids: number[] }) => {
      currentPage = page([row(2)]); // a teammate's edit kept row 2 open
      return body.ids.map((id) => row(id, "resolved"));
    });

    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    await waitFor(() => expect(screen.getByText("1 selected")).toBeInTheDocument());
    expect(await screen.findByRole("status")).toHaveTextContent(/1 triaged row left this view/i);
  });
});

describe("ExceptionsWorkspace CSV export failures (#294)", () => {
  it("surfaces a 403 through the toast instead of silently returning to idle", async () => {
    renderWorkspace("");
    await screen.findByLabelText("Select exception 1");
    mocks.download.mockRejectedValue(new ApiError(403, "Not enough permissions"));

    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/permission/i);
    // Button returns to idle so the analyst can retry.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /export csv/i })).not.toBeDisabled(),
    );
  });

  it("surfaces a network failure too", async () => {
    renderWorkspace("");
    await screen.findByLabelText("Select exception 1");
    mocks.download.mockRejectedValue(new TypeError("Failed to fetch"));

    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/connection/i);
  });
});
