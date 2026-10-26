import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { BrowserRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import SettingsPage from "./SettingsPage";

const state = vi.hoisted(() => ({ role: "viewer" }));
vi.mock("../auth", () => ({ useAuth: () => ({ user: { role: state.role, email: "user@example.com" } }), isAdmin: (user: { role: string }) => user.role === "admin" }));
vi.mock("../api/client", () => ({ api: { get: () => Promise.resolve([]) } }));
vi.mock("../components/ApiTokensCard", () => ({ ApiTokensCard: () => <div>Machine token controls</div> }));
function show(role: string) {
  state.role = role;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<BrowserRouter><QueryClientProvider client={client}><SettingsPage /></QueryClientProvider></BrowserRouter>);
}
describe("Settings token controls", () => {
  it("hides token management from viewers", () => { show("viewer"); expect(screen.queryByText("Machine token controls")).not.toBeInTheDocument(); });
  it("shows token management to admins", () => { show("admin"); expect(screen.getByText("Machine token controls")).toBeInTheDocument(); });
});
