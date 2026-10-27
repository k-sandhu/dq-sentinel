import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ editable: true, get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
vi.mock("../../api/client", () => ({ api: { get: mocks.get, post: mocks.post, patch: mocks.patch } }));
vi.mock("../../auth", () => ({ useAuth: () => ({ user: {} }), canEdit: () => mocks.editable }));
import MonitorPackTab from "./MonitorPackTab";
const pack = { id: 1, dataset_id: 2, enabled: true, status: "ready", config: { monitors: {freshness: true, volume: true, schema: true, drift: true}, cadence: {}, sensitivity: {} }, managed_checks: [], reconciliation: null, last_error: "", last_reconciled_at: null };
function show() { const client = new QueryClient({defaultOptions: {queries: {retry: false}}}); render(<QueryClientProvider client={client}><MemoryRouter><MonitorPackTab datasetId={2} hasProfile={true} /></MemoryRouter></QueryClientProvider>); }
beforeEach(() => { mocks.editable = true; mocks.get.mockReset().mockResolvedValue(pack); mocks.post.mockReset().mockResolvedValue(pack); mocks.patch.mockReset().mockResolvedValue(pack); });
describe("monitor administration", () => {
 it("offers management actions within the disclosure and sends mutations", async () => { show(); await screen.findByText("Pack administration"); fireEvent.click(screen.getByText("Pack administration")); fireEvent.click(screen.getAllByText("Reconcile now")[0]); await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/datasets/2/monitor-pack/reconcile")); fireEvent.click(screen.getByText("Disable pack")); await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/datasets/2/monitor-pack", {enabled: false})); fireEvent.click(screen.getByText("Configure")); expect(screen.getByText("Configure monitor pack")).toBeTruthy(); });
 it("does not offer mutations to viewers", async () => { mocks.editable = false; show(); await screen.findByText("Pack administration"); fireEvent.click(screen.getByText("Pack administration")); expect(screen.getByText("viewer: read only")).toBeTruthy(); expect(screen.queryByText("Configure")).toBeNull(); expect(screen.queryByText("Disable pack")).toBeNull(); expect(mocks.post).not.toHaveBeenCalled(); expect(mocks.patch).not.toHaveBeenCalled(); });
});
