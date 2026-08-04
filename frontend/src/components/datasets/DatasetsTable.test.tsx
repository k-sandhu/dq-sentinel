import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { Dataset } from "../../api/types";
import { DatasetsTable } from "./DatasetsTable";

const makeDataset = (o: Partial<Dataset>): Dataset =>
  ({
    id: 1,
    connection_id: 1,
    connection_name: "warehouse",
    schema_name: "public",
    table_name: "orders",
    display_name: "orders",
    row_count: 0,
    last_profiled_at: null,
    created_at: "2026-01-01T00:00:00Z",
    active_checks: 0,
    open_exceptions: 0,
    health: "fail",
    monitoring: "ok",
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

function renderTable(props: Partial<Parameters<typeof DatasetsTable>[0]> = {}) {
  const onToggleFav = vi.fn((e: { stopPropagation: () => void }) => e.stopPropagation());
  const onNavigate = vi.fn();
  render(
    <MemoryRouter>
      <DatasetsTable
        data={[makeDataset({ id: 7, table_name: "orders", health: "fail" })]}
        favSet={new Set()}
        onToggleFav={onToggleFav}
        onNavigate={onNavigate}
        {...props}
      />
    </MemoryRouter>,
  );
  return { onToggleFav, onNavigate };
}

describe("DatasetsTable", () => {
  it("renders the status word (never colour-only) for each row", () => {
    renderTable();
    expect(screen.getByText("fail")).toBeInTheDocument();
  });

  it("reflects favorite state on the star button's aria-pressed + label", () => {
    renderTable({ favSet: new Set([7]) });
    const star = screen.getByRole("button", { name: /remove orders from favorites/i });
    expect(star).toHaveAttribute("aria-pressed", "true");
  });

  it("toggles a favorite without navigating into the row (stopPropagation)", () => {
    const { onToggleFav, onNavigate } = renderTable();
    fireEvent.click(screen.getByRole("button", { name: /add orders to favorites/i }));
    expect(onToggleFav).toHaveBeenCalledWith(expect.anything(), 7);
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("navigates on a row click", () => {
    const { onNavigate } = renderTable();
    fireEvent.click(screen.getByText("fail")); // a non-link cell in the row
    expect(onNavigate).toHaveBeenCalledWith(7);
  });

  it("does not double-navigate when the row-title link is clicked (its own stopPropagation)", () => {
    const { onNavigate } = renderTable();
    fireEvent.click(screen.getByRole("link", { name: /orders/i }));
    expect(onNavigate).not.toHaveBeenCalled();
  });

  // #262: an errored check never evaluated the data, so it captures nothing to
  // triage. A dataset in that state must read as broken monitoring (REPAIR), not
  // as a red data-quality verdict (TRIAGE).
  it("reads a wholly-errored dataset as broken monitoring, linking to the errored run", () => {
    renderTable({
      data: [
        makeDataset({
          id: 7,
          health: "fail",
          active_checks: 16,
          errored_checks: 16,
          failing_checks: 0,
          open_exceptions: 0,
          monitoring: "broken",
          last_error: "authentication with the source failed",
          last_error_run_id: 42,
        }),
      ],
    });
    const pill = screen.getByRole("link", { name: "checks broken" });
    expect(pill).toHaveAttribute("href", "/runs/42");
    expect(pill).toHaveAccessibleDescription(/16 of 16 active checks could not run/);
    expect(screen.queryByText("fail")).not.toBeInTheDocument();
  });

  it("keeps the data-quality verdict and adds a repair chip when both are true", () => {
    renderTable({
      data: [
        makeDataset({
          id: 7,
          health: "fail",
          active_checks: 5,
          failing_checks: 2,
          errored_checks: 3,
          monitoring: "degraded",
          last_error_run_id: null,
        }),
      ],
    });
    expect(screen.getByText("fail")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "3 not running" })).toHaveAttribute(
      "href",
      "/datasets/7/runs",
    );
  });

  // The wording must come from `monitoring`, not from whether the pill got
  // suppressed. `broken` means EVERY active check errors; `degraded` means some
  // still run. Calling a degraded dataset "checks broken" overstates the outage
  // in exactly the direction #262 exists to stop.
  it("calls a partly-errored dataset degraded, not broken, even with no real failures", () => {
    renderTable({
      data: [
        makeDataset({
          id: 7,
          health: "fail", // any errored active check folds into `fail` server-side
          active_checks: 5,
          failing_checks: 0, // the other two pass — the verdict is owed to errors alone
          errored_checks: 3,
          monitoring: "degraded",
          last_error_run_id: 42,
        }),
      ],
    });
    expect(screen.getByRole("link", { name: "3 not running" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "checks broken" })).not.toBeInTheDocument();
    expect(screen.queryByText("fail")).not.toBeInTheDocument(); // verdict is entirely errors
  });

  // Errored checks capture nothing new, but exceptions captured before the source
  // broke are still open and still triageable — the chip must not tell the analyst
  // there is nothing to do while the same row shows a non-zero open count.
  it("does not claim there is nothing to triage while exceptions are open", () => {
    renderTable({
      data: [
        makeDataset({
          id: 7,
          health: "fail",
          active_checks: 4,
          failing_checks: 0,
          errored_checks: 4,
          open_exceptions: 40,
          monitoring: "broken",
          last_error_run_id: 42,
        }),
      ],
    });
    const pill = screen.getByRole("link", { name: "checks broken" });
    expect(pill).toHaveAccessibleDescription(/4 of 4 active checks could not run/);
    expect(pill).not.toHaveAccessibleDescription(/nothing to triage/);
    expect(pill).toHaveAccessibleDescription(/40 open exceptions/);
  });

  // The guard this replaces used `health: "warn"` with `errored_checks: 1` — a
  // payload the API cannot emit, since `serialize.dataset_out` folds any errored
  // active check into `fail`. A non-fail verdict therefore always comes with zero
  // errored checks; that is the shape worth pinning.
  it("never swallows a non-fail verdict — only a fail owed entirely to errors", () => {
    renderTable({
      data: [
        makeDataset({
          id: 7,
          health: "warn",
          active_checks: 4,
          failing_checks: 0,
          errored_checks: 0,
          monitoring: "ok",
        }),
      ],
    });
    expect(screen.getByText("warn")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /not running|checks broken/ })).not.toBeInTheDocument();
  });
});
