import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes, useLocation, useParams } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Check } from "../api/types";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn(), editable: true }));
vi.mock("../api/client", () => ({ api: mocks }));
vi.mock("../auth", () => ({ useAuth: () => ({ user: { role: mocks.editable ? "editor" : "viewer" } }), canEdit: () => mocks.editable }));
import ChecksPage from "../pages/ChecksPage";
import ChecksTab from "../pages/dataset/ChecksTab";
import { ConfirmProvider } from "./confirm";
import { checkView, orderCheckResults } from "./CheckViews";

function check(id: number, overrides: Partial<Check> = {}): Check {
  return { id, dataset_id: 3, dataset_name: "orders", name: `Check ${id}`, check_type: "not_null", column_name: "amount", params: {}, severity: "error", status: "active", origin: "heuristic", rationale: "Profile suggests required values", schedule_kind: "interval", schedule_expr: "1440", next_run_at: null, last_run_at: null, last_status: "pass", created_at: "2026-10-26T00:00:00Z", ...overrides };
}
const checks = [check(1), check(2, { status: "proposed", name: "Proposed amount" }), check(3, { last_status: "fail", name: "Failing amount" }), check(4, { last_status: "warn", name: "Warning amount", status: "disabled" })];
function Location() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
}
function DatasetRoute() {
  const { id } = useParams();
  return <><Link to="/datasets/3/profile">Profile</Link><Link to="/datasets/4/checks">Other dataset</Link><Link to={`/datasets/${id}/checks?context=latest`}>Update context</Link><ChecksTab datasetId={Number(id)} hasProfile /></>;
}
function deferredGeneration() {
  let resolve!: (value: unknown) => void;
  mocks.post.mockReturnValue(new Promise((done) => { resolve = done; }));
  return async () => { await act(async () => { resolve({ created: 1, mode: "heuristic", skipped_duplicates: 0, explored: false }); }); };
}
const datasetRoutes = <Routes><Route path="/datasets/:id/checks" element={<DatasetRoute />} /><Route path="/datasets/:id/profile" element={<div>Profile page</div>} /></Routes>;
function mount(ui: React.ReactNode, at = "/checks") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<MemoryRouter initialEntries={[at]}><QueryClientProvider client={client}><ConfirmProvider>{ui}<Location /></ConfirmProvider></QueryClientProvider></MemoryRouter>);
}
beforeEach(() => {
  mocks.editable = true;
  mocks.get.mockImplementation((path: string) => Promise.resolve(path === "/health" ? { llm_enabled: false } : path.startsWith("/checks") ? checks : []));
});
afterEach(() => vi.clearAllMocks());

describe("Check results and proposals", () => {
  it("defaults to results, counts isolated proposals, and sorts failures before warnings and passes", async () => {
    mount(<ChecksPage />);
    await screen.findByRole("link", { name: "Failing amount" });
    expect(screen.getByRole("button", { name: "Results" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Proposals (1)" })).toBeInTheDocument();
    expect(screen.queryByText("Proposed amount")).not.toBeInTheDocument();
    const rows = screen.getAllByRole("row").filter((row) => within(row).queryByRole("link", { name: /Failing amount|Warning amount|Check 1/ }));
    expect(rows.map((row) => within(row).getAllByRole("link")[0].textContent)).toEqual(["Failing amount", "Warning amount", "Check 1"]);
    expect(screen.getByRole("rowheader", { name: "Failing · orders · amount" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Proposals (1)" }));
    expect(screen.queryByRole("link", { name: "Failing amount" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate all (1)" })).toBeInTheDocument();
  });

  it("keeps old proposal links working, preserves unrelated params and supports paused results", async () => {
    mount(<ChecksPage />, "/checks?status=proposed&context=keep");
    await screen.findByRole("button", { name: "Activate all (1)" });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));
    expect(screen.getByTestId("location")).toHaveTextContent("context=keep");
    expect(screen.getByTestId("location")).not.toHaveTextContent("status=proposed");
    await userEvent.click(screen.getByRole("button", { name: "Paused" }));
    expect(screen.getByRole("link", { name: "Warning amount" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Failing amount" })).not.toBeInTheDocument();
  });

  it("lets viewers inspect proposals without mutation controls", async () => {
    mocks.editable = false;
    mount(<ChecksPage />, "/checks?view=proposals");
    await screen.findByText("Rationale & parameters");
    expect(screen.queryByRole("button", { name: /Activate|Dismiss|Edit|Run/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Rationale & parameters"));
    expect(screen.getByText("Profile suggests required values")).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent("/checks?view=proposals");
  });

  it("preserves result filters through a Proposals roundtrip", async () => {
    mount(<ChecksPage />, "/checks?status=active&last_status=fail&q=amount&context=keep");
    await screen.findByRole("link", { name: "Failing amount" });
    await userEvent.click(screen.getByRole("button", { name: "Proposals (1)" }));
    await screen.findByRole("button", { name: "Activate all (1)" });
    expect(screen.getByTestId("location")).toHaveTextContent("status=active&last_status=fail&q=amount&context=keep");
    await userEvent.click(screen.getByRole("button", { name: "Results" }));
    expect(screen.getByRole("button", { name: "Active" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("link", { name: "Failing amount" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Warning amount" })).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("status=active&last_status=fail&q=amount&context=keep");
  });

  it("opens action and rationale disclosures without following the clickable row", async () => {
    mount(<ChecksPage />);
    await screen.findByRole("link", { name: "Failing amount" });
    const summary = screen.getByLabelText("More actions for Failing amount");
    await userEvent.click(summary);
    expect(summary.closest("details")).toHaveAttribute("open");
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/checks$/);
    await userEvent.click(screen.getByRole("button", { name: "Proposals (1)" }));
    await userEvent.click(screen.getByText("Rationale & parameters"));
    expect(screen.getByText("Profile suggests required values")).toBeVisible();
    await userEvent.click(screen.getByText("Profile suggests required values"));
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/checks\?view=proposals$/);
  });

  it("switches generation to proposal review without activating, including heuristic mode", async () => {
    mocks.post.mockResolvedValue({ created: 1, mode: "heuristic", skipped_duplicates: 0, explored: false });
    mount(<ChecksTab datasetId={3} hasProfile />, "/datasets/3/checks?context=keep&last_status=fail&q=old");
    await userEvent.click(await screen.findByRole("button", { name: "Generate checks" }));
    await screen.findByRole("button", { name: "Activate all (1)" });
    expect(screen.getByRole("button", { name: "Proposals (1)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("location")).toHaveTextContent("context=keep");
    expect(screen.getByTestId("location")).not.toHaveTextContent("last_status");
    expect(mocks.post).toHaveBeenCalledExactlyOnceWith("/checks/generate", { dataset_id: 3, use_llm: false, explore: false });
    expect(mocks.patch).not.toHaveBeenCalled();
  });

  it("does not redirect after generation completes on an unmounted tab", async () => {
    const finish = deferredGeneration();
    mount(datasetRoutes, "/datasets/3/checks");
    await userEvent.click(await screen.findByRole("button", { name: "Generate checks" }));
    await userEvent.click(screen.getByRole("link", { name: "Profile" }));
    await screen.findByText("Profile page");
    await finish();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/datasets\/3\/profile$/);
    expect(screen.queryByText(/Generated/)).not.toBeInTheDocument();
  });

  it("does not show an old dataset's generation feedback or switch its replacement to proposals", async () => {
    const finish = deferredGeneration();
    mount(datasetRoutes, "/datasets/3/checks");
    await userEvent.click(await screen.findByRole("button", { name: "Generate checks" }));
    await userEvent.click(screen.getByRole("link", { name: "Other dataset" }));
    await finish();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/datasets\/4\/checks$/);
    expect(screen.getByRole("button", { name: "Results" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/Generated/)).not.toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledExactlyOnceWith("/checks/generate", { dataset_id: 3, use_llm: false, explore: false });
  });

  it("preserves params updated while generation is pending on the same dataset", async () => {
    const finish = deferredGeneration();
    mount(datasetRoutes, "/datasets/3/checks?context=old");
    await userEvent.click(await screen.findByRole("button", { name: "Generate checks" }));
    await userEvent.click(screen.getByRole("link", { name: "Update context" }));
    await finish();
    expect(screen.getByTestId("location")).toHaveTextContent("context=latest&view=proposals");
    expect(screen.getByRole("button", { name: "Proposals (1)" })).toHaveAttribute("aria-pressed", "true");
  });

  it("orders errors, failures, warnings, unknowns and passes, then groups columns", () => {
    expect(orderCheckResults([check(1), check(2, { last_status: "fail", column_name: "z" }), check(3, { last_status: "error" }), check(4, { last_status: null }), check(5, { last_status: "warn" }), check(6, { last_status: "fail", column_name: "a" })], false).map((c) => c.id)).toEqual([3, 6, 2, 5, 4, 1]);
    expect(checkView(new URLSearchParams("view=invalid"))).toBe("results");
    expect(checkView(new URLSearchParams("view=results&status=proposed"))).toBe("results");
  });
});
