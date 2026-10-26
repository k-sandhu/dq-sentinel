import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getPref, PREF_KEYS, setPref } from "../../lib/prefs";
import type { SavedView } from "../../lib/prefs";
import { ConfirmProvider } from "../confirm";
import SavedViews from "./SavedViews";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { ...actual.api, get: vi.fn().mockResolvedValue({ my_open: 1, new_today: 2, high_severity: 3, recurring: 4, unassigned: 5, expected: 6 }) } };
});

function Harness({ initial }: { initial: string }) {
  const [params, setParams] = useState(initial);
  return <><SavedViews currentParams={params} onApply={setParams} /><output data-testid="params">{params}</output></>;
}

function mount(views: SavedView[], initial = "") {
  setPref(PREF_KEYS.views, views);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
    <MemoryRouter><ConfirmProvider><Harness initial={initial} /></ConfirmProvider></MemoryRouter>
  </QueryClientProvider>);
}

beforeEach(() => setPref(PREF_KEYS.views, []));

describe("Personal saved view identity", () => {
  it("matches and selects views with different ordering and grouping", async () => {
    const first = "status=open&sort=oldest&group=check";
    const second = "status=open&sort=severity&group=dataset";
    mount([{ name: "Oldest by check", params: first }, { name: "Severity by dataset", params: second }], second);
    const picker = screen.getByLabelText("Saved views");
    expect(picker).toHaveValue("saved:Severity by dataset");
    fireEvent.change(picker, { target: { value: "saved:Oldest by check" } });
    expect(screen.getByTestId("params")).toHaveTextContent(first);
    fireEvent.change(picker, { target: { value: "saved:Severity by dataset" } });
    expect(picker).toHaveValue("saved:Severity by dataset");
    expect(screen.getByTestId("params")).toHaveTextContent(second);
    fireEvent.click(screen.getByRole("button", { name: "Delete saved view Severity by dataset" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(getPref<SavedView[]>(PREF_KEYS.views, []).map((v) => v.name)).toEqual(["Oldest by check"]));
    expect(screen.queryByRole("option", { name: "Severity by dataset" })).not.toBeInTheDocument();
  });

  it("retains identical aliases by name and deletes the intended alias after indexes shift", async () => {
    const params = "status=open&group=check";
    mount([{ name: "First", params }, { name: "Second: alias", params }, { name: "Third", params }], params);
    const picker = screen.getByLabelText("Saved views");
    fireEvent.change(picker, { target: { value: "saved:Second: alias" } });
    expect(picker).toHaveValue("saved:Second: alias");
    fireEvent.click(screen.getByRole("button", { name: "Delete saved view Second: alias" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(getPref<SavedView[]>(PREF_KEYS.views, []).map((v) => v.name)).toEqual(["First", "Third"]));
    fireEvent.change(picker, { target: { value: "saved:Third" } });
    expect(picker).toHaveValue("saved:Third");
    fireEvent.click(screen.getByRole("button", { name: "Delete saved view Third" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(getPref<SavedView[]>(PREF_KEYS.views, []).map((v) => v.name)).toEqual(["First"]));
  });

  it("matches default presentation while ignoring paging and selected exception", () => {
    mount([{ name: "Default presentation", params: "status=open&sort=newest&group=none" }], "status=open&offset=50&sel=8");
    expect(screen.getByLabelText("Saved views")).toHaveValue("saved:Default presentation");
  });
});
