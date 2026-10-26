import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import DocsLauncher from "./DocsLauncher";
import { ConfirmProvider } from "./confirm";
import { useUnsavedGuard } from "../lib/useUnsavedGuard";

function DirtyEditorWithDocs() {
  useUnsavedGuard(true);
  const location = useLocation();
  return <><DocsLauncher /><div>Editor route: {location.pathname}</div></>;
}

describe("header documentation disclosure", () => {
  it("keeps a focused reference link mounted when the pointer leaves", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><DocsLauncher /></MemoryRouter>);
    const trigger = screen.getByRole("button", { name: "Open documentation and features" });
    await user.click(trigger);
    await user.tab();
    const documentation = screen.getByRole("link", { name: /Documentation Guides/ });
    expect(documentation).toHaveFocus();
    fireEvent.mouseLeave(trigger.parentElement!);
    expect(documentation).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
  });

  it.each([/Documentation Guides/, /Features Everything/])("restores trigger focus when guarded navigation to %s is cancelled", async (name) => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/editor"]}><ConfirmProvider><DirtyEditorWithDocs /></ConfirmProvider></MemoryRouter>);
    const trigger = screen.getByRole("button", { name: "Open documentation and features" });
    await user.click(trigger);
    screen.getByRole("link", { name }).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Editor route: /editor")).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps hidden links out of keyboard navigation and restores focus on Escape", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><DocsLauncher /></MemoryRouter>);
    const trigger = screen.getByRole("button", { name: "Open documentation and features" });
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    await user.tab();
    expect(trigger).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    await user.tab();
    expect(screen.getByRole("link", { name: /Documentation Guides/ })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("closes after focus leaves the reference links", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><DocsLauncher /><button>Next control</button></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "Open documentation and features" }));
    await user.tab();
    await user.tab();
    expect(screen.getByRole("link", { name: /Features Everything/ })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Next control" })).toHaveFocus();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
