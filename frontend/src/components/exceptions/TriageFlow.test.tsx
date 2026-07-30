// Keyboard triage + bulk actions in the exceptions workspace (#293).
//
// Triage is the flow analysts spend their day in, and the 2026-07 UX benchmark's
// P1/P2s clustered on it: actions firing on the wrong rows, actions firing while
// the analyst was typing, and state disappearing without a word. Those are all
// *behavioural* properties of the document-level key handler and the bulk bar,
// so they are exercised here through the real workspace.
//
// Deliberately NOT retested here (ExceptionsWorkspace.test.tsx owns them):
// post-triage selection pruning (#286) and the CSV-export failure path (#294).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExceptionPage, ExceptionRecord, Role } from "../../api/types";
import ExceptionsWorkspace from "./ExceptionsWorkspace";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  download: vi.fn(),
  role: { current: "editor" as Role },
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
      user: {
        id: 7,
        email: "ed@example.com",
        name: "Ed",
        role: mocks.role.current,
        is_active: true,
        created_at: "2026-01-01T00:00:00Z",
      },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const ASSIGNEES = [{ id: 9, email: "dana@example.com", name: "Dana" }];

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

let currentPage: ExceptionPage = { items: [], total: 0, limit: 50, offset: 0 };

beforeAll(() => {
  // jsdom has no layout engine, so Element.scrollIntoView is undefined — j/k
  // call it to keep the focused row on screen. Without this stub the handler
  // throws inside the listener and the assertion failures are unreadable.
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.role.current = "editor";
  const items = [row(1), row(2), row(3)];
  currentPage = { items, total: items.length, limit: 50, offset: 0 };
  mocks.get.mockImplementation(async (path: string) => {
    if (path.startsWith("/exceptions/facets")) {
      return { status: {}, severity: {}, check_type: {}, datasets: [], total: currentPage.total };
    }
    if (path.startsWith("/exceptions/view-counts")) {
      return { my_open: 0, new_today: 0, high_severity: 0, recurring: 0, unassigned: 0, expected: 0 };
    }
    if (path.includes("/events")) return [];
    if (path.includes("/attribution")) return { factors: [], rows: [] };
    if (path.startsWith("/exceptions")) return currentPage;
    if (path.startsWith("/auth/assignees")) return ASSIGNEES;
    if (path.startsWith("/checks/types")) return [];
    if (path.startsWith("/checks")) return [];
    throw new Error(`unexpected GET ${path}`);
  });
  mocks.post.mockImplementation(async (_path: string, body: { ids: number[] }) =>
    body.ids.map((id) => row(id, "acknowledged")),
  );
});

async function renderWorkspace(search = "") {
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
  await screen.findByText("row 1 violates not_null");
  // The workspace parks keyboard focus on the first row once the page lands
  // (an effect, so it settles a tick after the rows paint). Every keyboard test
  // below starts from there.
  await waitFor(() => expect(rowEl(1)).toHaveAttribute("aria-selected", "true"));
}

/** The <tr> for a row, found through its reason cell. */
function rowEl(id: number): HTMLElement {
  return screen.getByText(`row ${id} violates not_null`).closest("tr") as HTMLElement;
}

const focusedIds = () =>
  [1, 2, 3].filter((id) => rowEl(id).getAttribute("aria-selected") === "true");

/** Press a key the way the app hears it: on document, from wherever focus is. */
function press(key: string, init: Partial<KeyboardEventInit> = {}) {
  fireEvent.keyDown(document.activeElement ?? document.body, { key, ...init });
}

const triagePayloads = () => mocks.post.mock.calls.map((c) => c[1]);

// ---------------------------------------------------------------------------
// focus movement
// ---------------------------------------------------------------------------

describe("keyboard row focus", () => {
  it("starts on the first row and moves with j / k", async () => {
    await renderWorkspace();
    expect(focusedIds()).toEqual([1]);

    press("j");
    expect(focusedIds()).toEqual([2]);
    press("j");
    expect(focusedIds()).toEqual([3]);
    press("k");
    expect(focusedIds()).toEqual([2]);
  });

  it("clamps at both ends instead of wrapping onto an unexpected row", async () => {
    await renderWorkspace();
    press("k");
    expect(focusedIds()).toEqual([1]);
    press("j");
    press("j");
    press("j");
    press("j");
    expect(focusedIds()).toEqual([3]);
  });
});

// ---------------------------------------------------------------------------
// selection + targeting
// ---------------------------------------------------------------------------

describe("keyboard selection", () => {
  it("x toggles the focused row into and out of the selection", async () => {
    await renderWorkspace();
    press("x");
    expect(await screen.findByText("1 selected")).toBeInTheDocument();

    press("x");
    await waitFor(() => expect(screen.queryByText(/\d+ selected/)).not.toBeInTheDocument());
  });

  it("applies a letter action to the whole selection", async () => {
    await renderWorkspace();
    press("x"); // row 1
    press("j");
    press("x"); // row 2
    expect(await screen.findByText("2 selected")).toBeInTheDocument();

    press("r");
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post.mock.calls[0][0]).toBe("/exceptions/triage");
    expect(triagePayloads()[0]).toMatchObject({ ids: [1, 2], status: "resolved" });
  });

  it("falls back to the focused row when nothing is selected", async () => {
    await renderWorkspace();
    press("j"); // focus row 2, select nothing
    press("a");
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(triagePayloads()[0]).toMatchObject({ ids: [2], status: "acknowledged" });
  });

  it("maps every documented triage letter to its status", async () => {
    await renderWorkspace();
    for (const [key, status] of [
      ["a", "acknowledged"],
      ["e", "expected"],
      ["r", "resolved"],
      ["m", "muted"],
      ["u", "open"],
    ] as const) {
      mocks.post.mockClear();
      press(key);
      await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
      expect(triagePayloads()[0]).toMatchObject({ ids: [1], status });
    }
  });

  it("Shift+A assigns to the signed-in user", async () => {
    await renderWorkspace();
    press("A", { shiftKey: true });
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(triagePayloads()[0]).toMatchObject({ ids: [1], assigned_to_id: 7 });
  });
});

// ---------------------------------------------------------------------------
// the guards — the shortcuts must not fire when the analyst meant something else
// ---------------------------------------------------------------------------

// Every `expect(mocks.post).not.toHaveBeenCalled()` below is preceded by
// `await act(async () => {})`. That is load-bearing, not ceremony: TanStack
// Query's Mutation.execute() awaits onMutate before the retryer ever calls
// mutationFn, so api.post is reached a microtask AFTER mutate() returns.
// Asserting synchronously after the keypress passes even when the guard is
// gone — verified by deleting all three guards from ExceptionsWorkspace and
// watching these four tests stay green. Draining the microtask queue first is
// what makes the assertion mean something. Do not remove these.
describe("keyboard shortcut guards", () => {
  it("does not triage while the analyst is typing a bulk note", async () => {
    await renderWorkspace();
    press("x");
    const note = await screen.findByLabelText("Bulk triage note");
    (note as HTMLInputElement).focus();

    // "resolve", typed into the note box, must stay text.
    for (const key of ["r", "e", "s", "o", "l", "v", "e"]) press(key);
    fireEvent.change(note, { target: { value: "resolve" } });

    await act(async () => {});
    expect(mocks.post).not.toHaveBeenCalled();
    expect(note).toHaveValue("resolve");
    expect(screen.getByText("1 selected")).toBeInTheDocument(); // "x" didn't toggle either
  });

  it("does not triage while the analyst is typing in the search box", async () => {
    await renderWorkspace();
    const search = screen.getByPlaceholderText(/search/i);
    (search as HTMLInputElement).focus();
    press("m");
    await act(async () => {});
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("never swallows a browser modifier combo (Ctrl+R is a reload, not a resolve)", async () => {
    await renderWorkspace();
    press("r", { ctrlKey: true });
    press("r", { metaKey: true });
    press("a", { altKey: true });
    await act(async () => {});
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("gives a viewer no triage surface at all", async () => {
    mocks.role.current = "viewer";
    await renderWorkspace();

    expect(screen.queryByLabelText("Select exception 1")).not.toBeInTheDocument();
    press("x");
    press("r");
    press("A", { shiftKey: true });
    await act(async () => {});
    expect(mocks.post).not.toHaveBeenCalled();
    expect(screen.queryByText(/\d+ selected/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// escape / panel / help
// ---------------------------------------------------------------------------

describe("Escape, the detail panel and the help sheet", () => {
  it("Escape clears the selection when no panel is open", async () => {
    await renderWorkspace();
    press("x");
    expect(await screen.findByText("1 selected")).toBeInTheDocument();

    press("Escape");
    await waitFor(() => expect(screen.queryByText(/\d+ selected/)).not.toBeInTheDocument());
  });

  it("Escape closes the panel first and keeps the selection intact", async () => {
    await renderWorkspace();
    press("x");
    await screen.findByText("1 selected");

    press("o"); // open the focused row's panel
    await screen.findByRole("complementary");

    press("Escape");
    await waitFor(() => expect(screen.queryByRole("complementary")).not.toBeInTheDocument());
    // the analyst's selection survived closing the drawer
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("? toggles the shortcut cheat-sheet", async () => {
    await renderWorkspace();
    press("?");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Keyboard shortcuts")).toBeInTheDocument();
    expect(within(dialog).getByText("Assign to me")).toBeInTheDocument();

    press("?");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

// ---------------------------------------------------------------------------
// bulk bar
// ---------------------------------------------------------------------------

describe("bulk actions", () => {
  async function selectTwo() {
    await renderWorkspace();
    fireEvent.click(screen.getByLabelText("Select exception 1"));
    fireEvent.click(screen.getByLabelText("Select exception 2"));
    await screen.findByText("2 selected");
  }

  it("sends the note with the status and then empties the note box", async () => {
    await selectTwo();
    const note = screen.getByLabelText("Bulk triage note");
    fireEvent.change(note, { target: { value: "known backfill gap" } });

    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(triagePayloads()[0]).toMatchObject({
      ids: [1, 2],
      status: "acknowledged",
      note: "known backfill gap",
    });
    // the note is per-action: leaving it behind would silently attach it to the
    // next, unrelated bulk action
    await waitFor(() => expect(note).toHaveValue(""));
  });

  it("assigns and unassigns the selection", async () => {
    await selectTwo();
    const assign = screen.getByLabelText("Assign selected");

    fireEvent.change(assign, { target: { value: "9" } });
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(triagePayloads()[0]).toMatchObject({ ids: [1, 2], assigned_to_id: 9 });

    fireEvent.change(assign, { target: { value: "__none" } });
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(triagePayloads()[1]).toMatchObject({ ids: [1, 2], clear_assignee: true });
    // the picker resets so it never claims a stale assignee
    expect(assign).toHaveValue("");
  });

  it("offers every triage action the keyboard does, labelled with its shortcut", async () => {
    await selectTwo();
    const bar = screen.getByRole("region", { name: "Bulk triage actions" });
    for (const [label, key] of [
      ["Acknowledge", "a"],
      ["Expected", "e"],
      ["Resolve", "r"],
      ["Mute", "m"],
      ["Reopen", "u"],
    ] as const) {
      expect(within(bar).getByRole("button", { name: label })).toHaveAttribute(
        "title",
        expect.stringContaining(`(${key})`),
      );
    }
  });

  it("Clear drops the selection without triaging anything", async () => {
    await selectTwo();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(screen.queryByText(/\d+ selected/)).not.toBeInTheDocument());
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("reports rows a teammate triaged first instead of pretending they were ours", async () => {
    await selectTwo();
    mocks.post.mockImplementation(async () => [row(1, "resolved")]); // only 1 of 2 came back

    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      /1 already triaged by someone else/i,
    );
  });
});
