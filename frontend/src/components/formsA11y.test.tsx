// Accessible names on the analyst workflow forms (#258 / #260).
//
// The QA pass found controls whose only identification was adjacent text or a
// placeholder — neither of which a screen reader ties to the input, and the
// placeholder disappears the moment you type. These tests pin the accessible
// *name* of each control that had none (or had a name polluted by its hint and
// error text), plus the grouping and the announced validation messages, using
// role+name queries so they fail precisely when the name goes missing again.
//
// The CheckParamsForm side of #258 is covered in CheckParamsForm.test.tsx.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() },
}));
vi.mock("../auth", () => ({
  useAuth: () => ({ user: { id: 1, email: "e@x.io", role: "editor" }, loading: false }),
  canEdit: () => true,
}));

import { api } from "../api/client";
import type { Check, CheckTypeInfo, Knowledge, Reliability, Sla } from "../api/types";
import ChecksTable from "./ChecksTable";
import { ConfirmProvider } from "./confirm";
import WidgetConfigModal, { defaultWidget } from "./dashboards/WidgetConfigModal";
import KnowledgeTab from "../pages/dataset/KnowledgeTab";
import ReliabilityPage from "../pages/ReliabilityPage";

afterEach(() => vi.clearAllMocks());

/** Every component here is a TanStack-Query consumer inside the router. */
function mount(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ConfirmProvider>{ui}</ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** api.get answers by path; anything unlisted resolves to an empty list. */
function mockGet(byPath: Record<string, unknown>) {
  vi.mocked(api.get).mockImplementation((path: string) =>
    Promise.resolve((byPath[path] ?? []) as never),
  );
}

// ---------------------------------------------------------------------------
// Edit check modal — the schedule pair
// ---------------------------------------------------------------------------

const CHECK: Check = {
  id: 11,
  dataset_id: 3,
  dataset_name: "public.orders",
  name: "fare_amount in range",
  check_type: "range",
  column_name: "fare_amount",
  params: { min: 0, max: 500 },
  severity: "warn",
  status: "active",
  origin: "heuristic",
  rationale: "profile bounds",
  schedule_kind: "interval",
  schedule_expr: "1440",
  next_run_at: null,
  last_run_at: null,
  last_status: null,
  created_at: "2026-07-01T00:00:00Z",
};

const RANGE_TYPE: CheckTypeInfo = {
  key: "range",
  label: "Range",
  description: "Values within bounds",
  needs_column: true,
  params: [
    { name: "min", type: "number", required: false, default: null, description: "Lower bound" },
    { name: "max", type: "number", required: false, default: null, description: "Upper bound" },
  ],
};

describe("Edit check — schedule", () => {
  async function openEditor() {
    const user = userEvent.setup();
    mockGet({ "/checks/types": [RANGE_TYPE] });
    mount(<ChecksTable checks={[CHECK]} showDataset={false} />);
    await user.click(screen.getByRole("button", { name: "Edit" }));
    return user;
  }

  // One caption ("Schedule") sat above two controls. A <label> names only the
  // first labelable descendant, so the expression box reached assistive tech
  // with no name at all — just a placeholder that vanishes on first keystroke.
  it("names the kind select and the expression box separately, inside one group", async () => {
    await openEditor();

    const group = screen.getByRole("group", { name: "Schedule" });
    expect(within(group).getByRole("combobox", { name: "Schedule kind" })).toHaveValue("interval");
    expect(within(group).getByRole("textbox", { name: "Interval in minutes" })).toHaveValue("1440");
  });

  it("renames the expression box when the schedule kind changes", async () => {
    const user = await openEditor();
    await user.selectOptions(screen.getByRole("combobox", { name: "Schedule kind" }), "cron");

    expect(screen.getByRole("textbox", { name: "Cron expression" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Interval in minutes" })).toBeNull();
  });

  it("still names the plain fields, and the params editor is a named group", async () => {
    await openEditor();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("fare_amount in range");
    expect(screen.getByRole("combobox", { name: "Severity" })).toHaveValue("warn");
    expect(screen.getByRole("group", { name: "Check parameters" })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Dashboard widget config
// ---------------------------------------------------------------------------

describe("Widget config modal", () => {
  const noop = () => {};

  it("names the status-matrix check search box (its caption names the picker)", async () => {
    mockGet({ "/datasets": [], "/checks?status=active": [] });
    mount(
      <WidgetConfigModal initial={defaultWidget("status_matrix", "w1")} onSave={noop} onClose={noop} />,
    );

    const picker = await screen.findByRole("group", { name: /^Checks \(0\/25\)/ });
    expect(within(picker).getByRole("textbox", { name: "Search checks" })).toBeInTheDocument();
  });

  it("names the note widget's markdown box", async () => {
    mockGet({ "/datasets": [] });
    mount(<WidgetConfigModal initial={defaultWidget("note", "w2")} onSave={noop} onClose={noop} />);

    expect(
      await screen.findByRole("textbox", { name: "Note content (Markdown)" }),
    ).toBeInTheDocument();
  });

  it("names the dataset multi-select as a group, not a loose pile of checkboxes", async () => {
    mockGet({ "/datasets": [] });
    mount(<WidgetConfigModal initial={defaultWidget("checks", "w3")} onSave={noop} onClose={noop} />);

    expect(await screen.findByRole("group", { name: "Datasets (up to 20)" })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Knowledge tab
// ---------------------------------------------------------------------------

const KNOWLEDGE: Knowledge = {
  business_context: "",
  known_issues: "",
  importance: "medium",
  owner: "",
  domain: "",
  team: "",
  freshness_sla_hours: null,
  slo_target_score: null,
  slo_window_days: null,
  slo_enabled: true,
  pii_columns: [],
  notes: "",
};

describe("Knowledge tab", () => {
  async function mountTab() {
    const user = userEvent.setup();
    mockGet({ "/datasets/28/knowledge": KNOWLEDGE });
    mount(<KnowledgeTab datasetId={28} />);
    // wait for the query to settle
    expect(await screen.findByRole("combobox", { name: "Importance" })).toHaveValue("medium");
    return user;
  }

  // The SLO toggle used to live in a <label> nested inside another <label>:
  // invalid HTML, and the checkbox's name became the whole block.
  it("names the SLO toggle 'Enabled' inside a 'Reliability SLO' group", async () => {
    await mountTab();

    const group = screen.getByRole("group", { name: "Reliability SLO" });
    const toggle = within(group).getByRole("checkbox", { name: "Enabled" });
    expect(toggle).toBeChecked();
    expect(toggle).toHaveAccessibleDescription(/Target source: importance default/);
  });

  it("keeps a field's name to its caption and its hint to the description", async () => {
    await mountTab();

    const freshness = screen.getByRole("spinbutton", { name: "Freshness SLA (hours)" });
    expect(freshness).toHaveAccessibleDescription(
      "Used as the threshold for generated freshness checks",
    );
    expect(screen.getByRole("textbox", { name: "PII columns" })).toHaveAccessibleDescription(
      /redacted before being sent to the LLM/,
    );
  });

  it("announces an out-of-range SLO target and links it to the input", async () => {
    const user = await mountTab();
    const target = screen.getByRole("spinbutton", { name: "Target score" });
    await user.type(target, "150");

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Use 0-100.");
    expect(target).toHaveAttribute("aria-invalid", "true");
    expect(target.getAttribute("aria-describedby")).toBe(alert.id);
  });

  it("announces a non-positive SLO window the same way", async () => {
    const user = await mountTab();
    const win = screen.getByRole("spinbutton", { name: "Window days" });
    await user.type(win, "-4");

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Use a positive number.");
    expect(win.getAttribute("aria-describedby")).toBe(alert.id);
  });
});

// ---------------------------------------------------------------------------
// Reliability — inline SLA editor
// ---------------------------------------------------------------------------

const SLA: Sla = {
  id: 7,
  name: "Orders freshness",
  scope: "dataset",
  scope_id: 3,
  target_type: "freshness",
  objective: 0.99,
  window: "rolling_30d",
  enabled: true,
  created_at: "2026-07-01T00:00:00Z",
  scope_label: "public.orders",
  dataset_id: 3,
  latest: null,
};

describe("SLA inline editor", () => {
  it("announces the blank-name error and ties it to the Name box", async () => {
    const user = userEvent.setup();
    mockGet({ "/sla/reliability": { total: 1, breached: 0, slas: [SLA] } satisfies Reliability });
    mount(<ReliabilityPage />);

    await user.click(await screen.findByRole("button", { name: /edit/i }));
    const form = screen.getByRole("form", { name: /edit sla orders freshness/i });
    const name = within(form).getByRole("textbox", { name: "Name" });
    expect(name).toHaveAttribute("aria-required", "true");

    await user.clear(name);
    const alert = within(form).getByRole("alert");
    expect(alert).toHaveTextContent("Name can't be empty.");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name.getAttribute("aria-describedby")).toBe(alert.id);
  });

  it("announces an out-of-range objective and ties it to the Objective box", async () => {
    const user = userEvent.setup();
    mockGet({ "/sla/reliability": { total: 1, breached: 0, slas: [SLA] } satisfies Reliability });
    mount(<ReliabilityPage />);

    await user.click(await screen.findByRole("button", { name: /edit/i }));
    const form = screen.getByRole("form", { name: /edit sla orders freshness/i });
    const objective = within(form).getByRole("spinbutton", { name: "Objective %" });
    await user.clear(objective);

    const alert = within(form).getByRole("alert");
    expect(alert).toHaveTextContent(/Objective must be greater than 0%/);
    expect(objective.getAttribute("aria-describedby")).toBe(alert.id);
  });

  it("marks the required dataset picker on the New SLA group", async () => {
    mockGet({ "/sla/reliability": { total: 0, breached: 0, slas: [] } satisfies Reliability });
    mount(<ReliabilityPage />);

    const group = await screen.findByRole("group", { name: "New SLA" });
    expect(within(group).getByRole("combobox", { name: "Dataset" })).toHaveAttribute(
      "aria-required",
      "true",
    );
  });
});
