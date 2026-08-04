import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() },
  getToken: () => null, // prefs then use the un-namespaced storage keys
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
// CodeMirror doesn't measure in jsdom; a textarea is enough to read/drive the SQL.
vi.mock("../components/workbench/SqlEditor", () => ({
  default: ({ value, onChange }: { value: string; onChange: (next: string) => void }) => (
    <textarea aria-label="SQL" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

import { api } from "../api/client";
import { PREF_KEYS } from "../lib/prefs";
import WorkbenchPage from "./WorkbenchPage";

const mockGet = vi.mocked(api.get);
const mockPost = vi.mocked(api.post);

const STALE_SQL = "SELECT status, COUNT(*) AS n\nFROM orders\nGROUP BY 1";

/** Datasets 28/31 sit on connections 2/3; connection 1 is the "previous" source. */
const dataset = (id: number, connectionId: number) => ({
  id,
  connection_id: connectionId,
  connection_name: `conn-${connectionId}`,
  schema_name: null,
  table_name: id === 28 ? "trips" : "payments",
});

function seedWorksheets(connectionId: number | null, sql = STALE_SQL) {
  localStorage.setItem(
    PREF_KEYS.workbenchTabs,
    JSON.stringify({ tabs: [{ id: "t1", title: "orders", sql }], activeId: "t1", connectionId }),
  );
}

function Nav({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(to)}>go</button>;
}

function renderWorkbench(entry: string) {
  mockGet.mockImplementation((path: string) => {
    if (path === "/connections")
      return Promise.resolve([
        { id: 1, name: "shopdb", kind: "sqlite" },
        { id: 2, name: "NYC Taxi public Jan 2024", kind: "duckdb" },
        { id: 3, name: "warehouse", kind: "postgresql" },
      ] as never);
    if (path === "/datasets/28") return Promise.resolve(dataset(28, 2) as never);
    if (path === "/datasets/31") return Promise.resolve(dataset(31, 3) as never);
    return Promise.resolve([] as never);
  });
  mockPost.mockResolvedValue({ mode: "heuristic", suggestions: [] } as never);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Nav to="/workbench?dataset_id=31" />
        <WorkbenchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const editor = () => screen.getByLabelText("SQL") as HTMLTextAreaElement;

beforeEach(() => localStorage.clear());
afterEach(() => vi.clearAllMocks());

// #255: the Workbench restores last session's worksheets from localStorage, but
// they were written against ONE source. Landing on a dataset that lives on a
// different connection left that SQL in the editor — header says `trips`, editor
// still holds an `orders` query, one Run away from the wrong database.
describe("WorkbenchPage dataset context", () => {
  it("drops restored SQL that belongs to another source", async () => {
    seedWorksheets(1); // worksheets written against connection 1
    renderWorkbench("/workbench?dataset_id=28"); // …but dataset 28 lives on connection 2
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveValue("2"));
    await waitFor(() => expect(editor().value).toBe(""));
    expect(screen.getByLabelText("Connection")).toHaveValue("2");
  });

  it("keeps restored SQL when the context is the source it was written against", async () => {
    seedWorksheets(2);
    renderWorkbench("/workbench?dataset_id=28");
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveValue("2"));
    expect(editor().value).toBe(STALE_SQL);
  });

  it("resumes the last session's source when the URL carries no context", async () => {
    seedWorksheets(3); // last session was on connection 3, not the first connection
    renderWorkbench("/workbench");
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveValue("3"));
    expect(editor().value).toBe(STALE_SQL);
  });

  it("clears a clean tab when the dataset context changes without a remount", async () => {
    seedWorksheets(2);
    renderWorkbench("/workbench?dataset_id=28");
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveValue("2"));
    expect(editor().value).toBe(STALE_SQL);
    fireEvent.click(screen.getByText("go")); // → dataset 31, connection 3
    await waitFor(() => expect(screen.getAllByText("payments").length).toBeGreaterThan(0));
    await waitFor(() => expect(editor().value).toBe(""));
  });

  it("never discards SQL the analyst has typed", async () => {
    seedWorksheets(2);
    renderWorkbench("/workbench?dataset_id=28");
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveValue("2"));
    expect(editor().value).toBe(STALE_SQL);
    fireEvent.change(editor(), { target: { value: "SELECT 1 -- mine" } });
    fireEvent.click(screen.getByText("go")); // → dataset 31, connection 3
    await waitFor(() => expect(screen.getAllByText("payments").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveValue("3"));
    expect(editor().value).toBe("SELECT 1 -- mine");
  });

  it("keeps the SQL when the analyst switches connection by hand", async () => {
    seedWorksheets(2);
    renderWorkbench("/workbench?dataset_id=28");
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveValue("2"));
    expect(editor().value).toBe(STALE_SQL);
    fireEvent.change(screen.getByLabelText("Connection"), { target: { value: "3" } });
    await waitFor(() => expect(screen.getByLabelText("Connection")).toHaveValue("3"));
    expect(editor().value).toBe(STALE_SQL);
  });
});
