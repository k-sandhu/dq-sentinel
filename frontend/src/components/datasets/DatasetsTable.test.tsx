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
});
