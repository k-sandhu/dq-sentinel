import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiTokensCard } from "./ApiTokensCard";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn(), confirm: vi.fn() }));
vi.mock("../api/client", () => ({ api: mocks }));
vi.mock("./confirm", () => ({ useConfirm: () => mocks.confirm }));

const token = { id: 4, name: "ci", role: "viewer", connection_scope: [1], created_at: "2026-01-01T00:00:00", expires_at: "2099-01-01T00:00:00", last_used_at: null, revoked_at: null, created_by_id: 1 };
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<BrowserRouter><QueryClientProvider client={client}><ApiTokensCard /></QueryClientProvider></BrowserRouter>);
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.get.mockImplementation((path: string) => Promise.resolve(path === "/connections" ? [{ id: 1, name: "Orders" }] : [token]));
  mocks.post.mockResolvedValue({ ...token, token: "dqs_once" });
  mocks.del.mockResolvedValue(undefined);
  mocks.confirm.mockResolvedValue(true);
});
describe("API tokens card", () => {
  it("requires explicit scope and shows the secret once", async () => {
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Create token" }));
    await user.type(screen.getByLabelText("Name"), "ci");
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    await user.click(await screen.findByLabelText("Orders"));
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("dqs_once")).toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledWith("/tokens", { name: "ci", role: "viewer", connection_scope: [1], expires_in_days: 400 });
    await user.click(screen.getAllByRole("button", { name: "Close" })[1]);
    await waitFor(() => expect(screen.queryByText("dqs_once")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Create token" }));
    expect(screen.queryByText("dqs_once")).not.toBeInTheDocument();
  });
  it("confirms revocation and honors cancellation", async () => {
    const user = userEvent.setup(); show();
    mocks.confirm.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const revoke = await screen.findByRole("button", { name: "Revoke" });
    await user.click(revoke); expect(mocks.del).not.toHaveBeenCalled();
    await user.click(revoke); await waitFor(() => expect(mocks.del).toHaveBeenCalledWith("/tokens/4"));
  });
  it("keeps the dialog open until creation returns its secret", async () => {
    const user = userEvent.setup(); show();
    let resolve!: (value: unknown) => void;
    mocks.post.mockReturnValueOnce(new Promise(r => { resolve = r; }));
    await user.click(await screen.findByRole("button", { name: "Create token" }));
    await user.type(screen.getByLabelText("Name"), "ci");
    await user.click(await screen.findByLabelText("Orders"));
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getAllByRole("button", { name: "Close" })[1]).toBeDisabled();
    await user.click(screen.getAllByRole("button", { name: "Close" })[0]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    resolve({ ...token, token: "dqs_once" });
    expect(await screen.findByText("dqs_once")).toBeInTheDocument();
  });
  it("renders UTC timestamps and connection scope metadata", async () => {
    mocks.get.mockImplementation((path: string) => Promise.resolve(path === "/connections"
      ? [{ id: 1, name: "Orders" }]
      : [{ ...token, expires_at: "2099-01-01T00:00:00Z", last_used_at: "2026-01-01T00:00:00Z" }]));
    show();
    expect(await screen.findByText("Connections: Orders (#1)")).toBeInTheDocument();
    expect(screen.getByText(/Expires:/)).not.toHaveTextContent("—");
    expect(screen.getByText(/Last used:/)).not.toHaveTextContent("—");
  });
  it("reports clipboard failures without losing the secret", async () => {
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Create token" }));
    await user.type(screen.getByLabelText("Name"), "ci");
    await user.click(await screen.findByLabelText("Orders"));
    await user.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("dqs_once");
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(new Error("denied"));
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Copy failed");
    expect(screen.getByText("dqs_once")).toBeInTheDocument();
  });
  it("shows creation errors and keeps the form", async () => {
    const user = userEvent.setup(); show();
    mocks.post.mockRejectedValueOnce(new Error("Creation failed"));
    await user.click(await screen.findByRole("button", { name: "Create token" }));
    await user.type(screen.getByLabelText("Name"), "ci");
    await user.click(await screen.findByLabelText("Orders"));
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Creation failed")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("ci");
  });
});
