// Guards the "never silently destroy analyst state" bar on the SLA inline editor:
// the toolbar "Edit" button must not be able to throw away typed edits that the
// form's own Cancel button protects with a confirm (FE-2 / UX-3).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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
import type { Reliability, Sla } from "../api/types";
import { ConfirmProvider } from "../components/confirm";
import ReliabilityPage from "./ReliabilityPage";

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

const RELIABILITY: Reliability = { total: 1, breached: 0, slas: [SLA] };

function renderPage() {
  vi.mocked(api.get).mockImplementation((path: string) =>
    Promise.resolve((path === "/sla/reliability" ? RELIABILITY : []) as never),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ConfirmProvider>
          <ReliabilityPage />
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** Opens the inline editor and dirties its Name field. */
async function openDirtyEditor() {
  const user = userEvent.setup();
  renderPage();
  const edit = await screen.findByRole("button", { name: /edit/i });
  await user.click(edit);
  const form = await screen.findByRole("form", { name: /edit sla orders freshness/i });
  const name = within(form).getByLabelText("Name");
  await user.clear(name);
  await user.type(name, "Orders freshness v2");
  expect(name).toHaveValue("Orders freshness v2");
  return { user, edit, name };
}

afterEach(() => vi.clearAllMocks());

describe("SLA inline editor — analyst state is never dropped silently", () => {
  it("the toolbar Edit button cannot collapse the editor over unsaved typing", async () => {
    const { user, edit, name } = await openDirtyEditor();

    // Edit is open-only: while the editor is up it is inert, so a second click
    // can't unmount the form (and its typed state) without asking.
    expect(edit).toBeDisabled();
    await user.click(edit);

    expect(screen.getByRole("form", { name: /edit sla orders freshness/i })).toBeInTheDocument();
    expect(name).toHaveValue("Orders freshness v2");
    expect(edit).not.toHaveAttribute("aria-expanded"); // no longer a toggle
  });

  it("Cancel is the single close path and asks before discarding", async () => {
    const { user, name } = await openDirtyEditor();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Discard changes?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(screen.queryByText("Discard changes?")).toBeNull());
    expect(name).toHaveValue("Orders freshness v2"); // kept

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(await screen.findByRole("button", { name: "Discard" }));
    await waitFor(() =>
      expect(screen.queryByRole("form", { name: /edit sla orders freshness/i })).toBeNull(),
    );
    expect(screen.getByRole("button", { name: /edit/i })).toBeEnabled();
  });
});
