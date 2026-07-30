// Lifecycle tests for the unsaved-changes guard (#284). The riskiest failure mode
// is not "the guard didn't fire" but "the guard wedged navigation shut", so most of
// these assert that the user can still get out: cancel -> try again -> confirm,
// bypass, and unmount-while-a-prompt-is-open all leave the router usable.
//
// No JSX here on purpose — this is a .ts file, so components are built with
// React.createElement.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement as h, useState } from "react";
import type { ReactNode } from "react";
import { Link, MemoryRouter, Route, Routes, useNavigate } from "react-router";
import { describe, expect, it } from "vitest";
import { ConfirmProvider } from "../components/confirm";
import { useUnsavedGuard } from "./useUnsavedGuard";

const MESSAGE = "Your unsaved test draft will be discarded.";

/** A page holding "unsaved" state, plus the two ways it can navigate away. */
function Editor({ dirty }: { dirty: boolean }) {
  const guard = useUnsavedGuard(dirty, MESSAGE);
  const navigate = useNavigate();
  return h(
    "div",
    null,
    h("button", { onClick: () => navigate("/other") }, "Leave"),
    h("button", { onClick: () => guard.bypass(() => navigate("/other")) }, "Leave via bypass"),
    h(Link, { to: "/other" }, "Leave via link"),
  );
}

/** Keeps the router mounted while the guarded editor comes and goes. */
function Host({ dirty }: { dirty: boolean }) {
  const [mounted, setMounted] = useState(true);
  const navigate = useNavigate();
  return h(
    "div",
    null,
    mounted ? h(Editor, { dirty }) : null,
    h("button", { onClick: () => setMounted(false) }, "Unmount editor"),
    h("button", { onClick: () => navigate("/other") }, "Leave from host"),
  );
}

function renderApp(element: ReactNode) {
  return render(
    h(
      ConfirmProvider,
      null,
      h(
        MemoryRouter,
        { initialEntries: ["/edit"] },
        h(
          Routes,
          null,
          h(Route, { path: "/edit", element }),
          h(Route, { path: "/other", element: h("div", null, "OTHER PAGE") }),
        ),
      ),
    ),
  );
}

const dialog = () => screen.queryByRole("dialog");

describe("useUnsavedGuard", () => {
  it("lets navigation through untouched when the editor is clean", async () => {
    const user = userEvent.setup();
    renderApp(h(Editor, { dirty: false }));

    await user.click(screen.getByRole("button", { name: "Leave" }));

    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
    expect(dialog()).not.toBeInTheDocument();
  });

  it("blocks, re-arms after Cancel, and proceeds on Confirm", async () => {
    const user = userEvent.setup();
    renderApp(h(Editor, { dirty: true }));

    // 1. dirty -> navigate: held back, asked with the caller's message.
    await user.click(screen.getByRole("button", { name: "Leave" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(MESSAGE)).toBeInTheDocument();
    expect(screen.queryByText("OTHER PAGE")).not.toBeInTheDocument();

    // 2. cancel: stay put, dialog closes.
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(dialog()).not.toBeInTheDocument());
    expect(screen.queryByText("OTHER PAGE")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Leave" })).toBeInTheDocument();

    // 3. navigate again: the guard must re-arm, not stay stuck open or give up.
    await user.click(screen.getByRole("button", { name: "Leave" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    // 4. confirm: the blocked navigation is replayed.
    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
    expect(dialog()).not.toBeInTheDocument();
  });

  it("blocks a sidebar-style <Link> click, not just programmatic navigate()", async () => {
    const user = userEvent.setup();
    renderApp(h(Editor, { dirty: true }));

    await user.click(screen.getByRole("link", { name: "Leave via link" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText("OTHER PAGE")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
  });

  it("bypass() navigates without prompting even while dirty", async () => {
    const user = userEvent.setup();
    renderApp(h(Editor, { dirty: true }));

    await user.click(screen.getByRole("button", { name: "Leave via bypass" }));

    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
    expect(dialog()).not.toBeInTheDocument();
  });

  it("stops guarding once the dirty editor unmounts", async () => {
    const user = userEvent.setup();
    renderApp(h(Host, { dirty: true }));

    // Guard is live while the editor is mounted.
    await user.click(screen.getByRole("button", { name: "Leave from host" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(dialog()).not.toBeInTheDocument());

    // Editor goes away -> the navigator must be handed back unpatched.
    await user.click(screen.getByRole("button", { name: "Unmount editor" }));
    await user.click(screen.getByRole("button", { name: "Leave from host" }));

    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
    expect(dialog()).not.toBeInTheDocument();
  });

  it("honours the pending navigation when the editor unmounts mid-prompt", async () => {
    const user = userEvent.setup();
    renderApp(h(Host, { dirty: true }));

    await user.click(screen.getByRole("button", { name: "Leave from host" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    // Unmount underneath the open dialog, then answer it: no crash, and the
    // navigation the user asked for still happens.
    await user.click(screen.getByRole("button", { name: "Unmount editor" }));
    await user.click(screen.getByRole("button", { name: "Discard changes" }));

    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
  });

  it("does not wedge navigation if the editor unmounts and the prompt is abandoned", async () => {
    const user = userEvent.setup();
    renderApp(h(Host, { dirty: true }));

    await user.click(screen.getByRole("button", { name: "Leave from host" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Unmount editor" }));
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(dialog()).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Leave from host" }));
    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
  });

  it("guards tab close only while dirty", async () => {
    const dispatchUnload = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };

    const clean = renderApp(h(Editor, { dirty: false }));
    expect(dispatchUnload()).toBe(false);
    clean.unmount();

    renderApp(h(Editor, { dirty: true }));
    expect(dispatchUnload()).toBe(true);
  });
});
