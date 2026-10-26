import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import type { Check, Run } from "../../api/types";
import { MonitorEvidence } from "./MonitorEvidence";
const check = { id: 1, dataset_id: 2, name: "Volume", check_type: "row_count_anomaly", params: {} } as Check;
const run = (id: number, overrides: Partial<Run> = {}) => ({ id, check_id: 1, dataset_id: 2, status: "pass", started_at: "2026-01-01T00:00:00Z", finished_at: null, metrics: { row_count: 42, lower_bound: 30, upper_bound: 50 }, ...overrides } as Run);
const show = (runs: Run[], error: unknown = null) => render(<MemoryRouter><MonitorEvidence checks={[check]} runs={runs} error={error} loading={false} /></MemoryRouter>);
describe("monitor evidence", () => {
 it("shows actual metric, bounds and history", () => { show([run(2), run(1)]); expect(screen.getAllByText("42 rows").length).toBeGreaterThan(0); expect(screen.getByText("30–50 rows")).toBeTruthy(); expect(screen.getByText("Recent history")).toBeTruthy(); });
 it("does not use another dataset or check", () => { show([run(1, { dataset_id: 3 }), run(2, { check_id: 99 })]); expect(screen.getByText("No evaluations yet.")).toBeTruthy(); });
 it("shows latest error", () => { show([run(2, { status: "error", metrics: {} }), run(1)]); expect(screen.getAllByText("Evaluation error").length).toBeGreaterThan(0); });
 it("shows missing metric and insufficient history", () => { show([run(1, { metrics: {} })]); expect(screen.getByText("Metric unavailable")).toBeTruthy(); expect(screen.getByText("Insufficient history for a trend.")).toBeTruthy(); });
 it("reports request errors", () => { show([], new Error("failed")); expect(screen.getByRole("alert")).toBeTruthy(); });
 it("never labels a historical run with current sigma", () => { const changed = { ...check, params: { sigma: 9 } }; render(<MemoryRouter><MonitorEvidence checks={[changed]} runs={[run(1, {metrics: {row_count: 42, z: 2}})]} error={null} loading={false} /></MemoryRouter>); expect(screen.getByText("Threshold or range unavailable")).toBeTruthy(); expect(screen.queryByText(/limit/)).toBeNull(); });
 it.each([
  ["freshness", {age_hours: 3, max_age_hours: 12}, "3 hours old", "Maximum 12 hours"],
  ["schema_contract", {current_count: 4, expected_count: 5}, "4 columns", "5 expected columns"],
  ["distribution_drift", {score: 0.3, threshold: 0.2}, "Score 0.3", "Threshold 0.2"],
 ])("shows recorded %s metrics", (kind, metrics, value, threshold) => { render(<MemoryRouter><MonitorEvidence checks={[{...check, check_type: kind}]} runs={[run(1, {metrics})]} error={null} loading={false} /></MemoryRouter>); expect(screen.getByText(value)).toBeTruthy(); expect(screen.getByText(threshold)).toBeTruthy(); });
 it("shows cold-start baseline state", () => { show([run(1, {metrics: {row_count: 42, note: "collecting adaptive baseline"}})]); expect(screen.getByText("collecting adaptive baseline")).toBeTruthy(); expect(screen.getByText("Threshold or range unavailable")).toBeTruthy(); });
 it("prioritizes failed evaluations and collapses recent history", () => { const failing = { ...check, id: 2, name: "Failed monitor" }; render(<MemoryRouter><MonitorEvidence checks={[check, failing]} runs={[run(1), run(2, {check_id: 2, status: "fail"}), run(3, {check_id: 2, status: "fail"})]} error={null} loading={false} /></MemoryRouter>); const cards = screen.getAllByRole("article"); expect(cards[0].textContent).toContain("Failed monitor"); const details = screen.getByText("Recent history").closest("details"); expect(details?.open).toBe(false); });
});
