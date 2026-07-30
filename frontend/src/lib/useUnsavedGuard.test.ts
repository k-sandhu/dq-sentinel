// Lifecycle tests for the unsaved-changes guard (#284). The riskiest failure mode
// is not "the guard didn't fire" but "the guard wedged navigation shut", so most of
// these assert that the user can still get out: cancel -> try again -> confirm,
// bypass, and unmount-while-a-prompt-is-open all leave the router usable.
//
// The second half of the file exists because of #297 (react-router 7.17 -> 7.18.2):
// the guard works by replacing the router *navigator's* own push/replace/go, an
// internal object reached through `UNSAFE_NavigationContext`. A router upgrade
// could rename that context, freeze the navigator, or route <Link>/useNavigate()
// around it — and every one of those failures is silent: the hook would no-op and
// analyst drafts would start disappearing again. So we assert the patch itself is
// installed and handed back, not just that blocking happens to work.
//
// No JSX here on purpose — this is a .ts file, so components are built with
// React.createElement.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement as h, useContext, useState } from "react";
import type { ContextType, ReactNode } from "react";
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
  UNSAFE_NavigationContext,
  useNavigate,
} from "react-router";
import { describe, expect, it } from "vitest";
import { ConfirmProvider } from "../components/confirm";
import { useUnsavedGuard } from "./useUnsavedGuard";

const MESSAGE = "Your unsaved test draft will be discarded.";

type NavigationContextValue = ContextType<typeof UNSAFE_NavigationContext>;
type RouterNavigator = NavigationContextValue["navigator"];

interface NavSnapshot {
  navigator: RouterNavigator;
  push: RouterNavigator["push"];
  replace: RouterNavigator["replace"];
  go: RouterNavigator["go"];
}

let captured: NavSnapshot | null = null;

/**
 * Captures the router's navigator and its pristine methods. Rendered as the first
 * child inside the router: React runs every render before any effect, so this
 * sighting is guaranteed to predate the guard's patch (which is installed in an
 * effect). Tests can then compare identities before/after.
 */
function NavigatorProbe() {
  const navigator = (useContext(UNSAFE_NavigationContext) as NavigationContextValue).navigator;
  if (!captured || captured.navigator !== navigator) {
    captured = {
      navigator,
      push: navigator.push,
      replace: navigator.replace,
      go: navigator.go,
    };
  }
  return null;
}

function navSnapshot(): NavSnapshot {
  if (!captured) throw new Error("NavigatorProbe never saw a navigator");
  return captured;
}

/**
 * True while the guard's interception is installed on the captured navigator.
 * All three methods are patched and unpatched together, so a partial state is
 * itself a bug — assert that before reporting.
 */
function isPatched(): boolean {
  const snap = navSnapshot();
  const patched = [
    snap.navigator.push !== snap.push,
    snap.navigator.replace !== snap.replace,
    snap.navigator.go !== snap.go,
  ];
  expect(new Set(patched).size).toBe(1);
  return patched[0];
}

/** A page holding "unsaved" state, plus the ways it can navigate away. */
function Editor({ dirty, name = "" }: { dirty: boolean; name?: string }) {
  const guard = useUnsavedGuard(dirty, MESSAGE);
  const navigate = useNavigate();
  const s = name ? ` ${name}` : "";
  return h(
    "div",
    null,
    h("button", { onClick: () => navigate("/other") }, `Leave${s}`),
    h("button", { onClick: () => navigate("/other", { replace: true }) }, `Leave via replace${s}`),
    h("button", { onClick: () => navigate(-1) }, `Leave via back${s}`),
    h("button", { onClick: () => guard.bypass(() => navigate("/other")) }, `Leave via bypass${s}`),
    // A bypass that navigates nowhere: used to prove the suspension is scoped to
    // the call and does not disarm the guard for the rest of the page's life.
    h("button", { onClick: () => guard.bypass(() => undefined) }, `Bypass nothing${s}`),
    h(Link, { to: "/other" }, `Leave via link${s}`),
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

/** Two independently-mountable guards sharing one navigator (nesting/restore order). */
function TwoEditors({ dirtyA, dirtyB }: { dirtyA: boolean; dirtyB: boolean }) {
  const [aMounted, setA] = useState(true);
  const [bMounted, setB] = useState(true);
  const navigate = useNavigate();
  return h(
    "div",
    null,
    aMounted ? h(Editor, { dirty: dirtyA, name: "A" }) : null,
    bMounted ? h(Editor, { dirty: dirtyB, name: "B" }) : null,
    h("button", { onClick: () => setA(false) }, "Unmount A"),
    h("button", { onClick: () => setB(false) }, "Unmount B"),
    h("button", { onClick: () => navigate("/other") }, "Leave from host"),
  );
}

interface RenderOptions {
  initialEntries?: string[];
  initialIndex?: number;
}

function renderApp(element: ReactNode, options: RenderOptions = {}) {
  const { initialEntries = ["/edit"], initialIndex } = options;
  captured = null;
  return render(
    h(
      ConfirmProvider,
      null,
      h(
        MemoryRouter,
        { initialEntries, initialIndex },
        h(NavigatorProbe),
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

  it("stays navigable after cancelling: a later <Link> click still works", async () => {
    const user = userEvent.setup();
    renderApp(h(Editor, { dirty: true }));

    // Cancel a programmatic navigation...
    await user.click(screen.getByRole("button", { name: "Leave" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(dialog()).not.toBeInTheDocument());

    // ...then leave by a different route entirely. A guard that only re-armed the
    // path it was cancelled on would strand every other exit.
    await user.click(screen.getByRole("link", { name: "Leave via link" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
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

  it("blocks navigator.replace(), not just push()", async () => {
    const user = userEvent.setup();
    renderApp(h(Editor, { dirty: true }));

    // navigate(to, { replace: true }) goes through navigator.replace — a separate
    // method, separately patched. Redirect-style exits use it.
    await user.click(screen.getByRole("button", { name: "Leave via replace" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText("OTHER PAGE")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
  });

  it("blocks navigator.go(), i.e. an in-app navigate(-1)", async () => {
    const user = userEvent.setup();
    // Depth-2 history so navigate(-1) has somewhere to land. (This is the
    // *programmatic* back button, not the browser chrome one — popstate is a
    // documented gap, see useUnsavedGuard.ts.)
    renderApp(h(Editor, { dirty: true }), {
      initialEntries: ["/other", "/edit"],
      initialIndex: 1,
    });

    await user.click(screen.getByRole("button", { name: "Leave via back" }));

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

  it("bypass() only suspends the guard for the duration of the call", async () => {
    const user = userEvent.setup();
    renderApp(h(Editor, { dirty: true }));

    // A bypass that navigates nowhere must not leave the guard disarmed — that
    // would silently re-open #284 for the rest of the page's life.
    await user.click(screen.getByRole("button", { name: "Bypass nothing" }));

    await user.click(screen.getByRole("button", { name: "Leave" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText("OTHER PAGE")).not.toBeInTheDocument();
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

    const dirty = renderApp(h(Editor, { dirty: true }));
    expect(dispatchUnload()).toBe(true);

    // ...and the listener must go with the editor, or every later page load
    // inherits a phantom "leave site?" prompt.
    dirty.unmount();
    expect(dispatchUnload()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Router-integration contract (#297). These assert the mechanism, not just the
// behaviour, so a future react-router bump that silently moves the choke point
// fails loudly here instead of in production.
// ---------------------------------------------------------------------------
describe("useUnsavedGuard / react-router navigator contract", () => {
  it("finds a navigator on the navigation context with push, replace and go", () => {
    renderApp(h(Editor, { dirty: false }));
    const snap = navSnapshot();

    expect(snap.navigator).toBeTruthy();
    expect(typeof snap.push).toBe("function");
    expect(typeof snap.replace).toBe("function");
    expect(typeof snap.go).toBe("function");
  });

  it("installs the interception on mount and restores the originals on unmount", async () => {
    const user = userEvent.setup();
    renderApp(h(Host, { dirty: true }));
    const snap = navSnapshot();

    // Mounted: all three methods replaced. If react-router ever froze the
    // navigator or stopped handing out the live object, this is where it shows.
    expect(isPatched()).toBe(true);

    await user.click(screen.getByRole("button", { name: "Unmount editor" }));

    // Unmounted: the *exact same function objects* handed back, not equivalents.
    expect(snap.navigator.push).toBe(snap.push);
    expect(snap.navigator.replace).toBe(snap.replace);
    expect(snap.navigator.go).toBe(snap.go);
    expect(isPatched()).toBe(false);
  });

  it("keeps the interception installed while a clean editor is mounted", () => {
    // The patch is not conditional on dirtiness (dirtiness is read through a ref
    // at call time), so a clean editor is still patched — and still transparent.
    renderApp(h(Editor, { dirty: false }));
    expect(isPatched()).toBe(true);
  });

  for (const first of ["A", "B"] as const) {
    const second = first === "A" ? "B" : "A";

    it(`two mounted guards share one patch; unmounting ${first} then ${second} restores it exactly once`, async () => {
      const user = userEvent.setup();
      renderApp(h(TwoEditors, { dirtyA: true, dirtyB: true }));
      const snap = navSnapshot();
      expect(isPatched()).toBe(true);

      // Both dirty: exactly one dialog, never a stack of them.
      await user.click(screen.getByRole("button", { name: "Leave from host" }));
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(screen.queryAllByRole("dialog")).toHaveLength(1);
      await user.click(screen.getByRole("button", { name: "Keep editing" }));
      await waitFor(() => expect(dialog()).not.toBeInTheDocument());

      // First unmount must NOT tear the shared patch down under the survivor.
      await user.click(screen.getByRole("button", { name: `Unmount ${first}` }));
      expect(isPatched()).toBe(true);
      await user.click(screen.getByRole("button", { name: "Leave from host" }));
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(screen.queryByText("OTHER PAGE")).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Keep editing" }));
      await waitFor(() => expect(dialog()).not.toBeInTheDocument());

      // Last one out restores the originals — not a previously-patched wrapper,
      // which is how double-patching corrupts a shared navigator.
      await user.click(screen.getByRole("button", { name: `Unmount ${second}` }));
      expect(snap.navigator.push).toBe(snap.push);
      expect(snap.navigator.replace).toBe(snap.replace);
      expect(snap.navigator.go).toBe(snap.go);

      await user.click(screen.getByRole("button", { name: "Leave from host" }));
      expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
      expect(dialog()).not.toBeInTheDocument();
    });
  }

  it("a clean sibling guard does not suppress a dirty one, and vice versa", async () => {
    const user = userEvent.setup();
    renderApp(h(TwoEditors, { dirtyA: false, dirtyB: true }));

    // A is clean, B is dirty: the navigation is still held back.
    await user.click(screen.getByRole("button", { name: "Leave from host" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(dialog()).not.toBeInTheDocument());

    // Drop the dirty one: the clean sibling keeps the patch installed but must
    // let everything through.
    await user.click(screen.getByRole("button", { name: "Unmount B" }));
    expect(isPatched()).toBe(true);

    await user.click(screen.getByRole("button", { name: "Leave from host" }));
    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
    expect(dialog()).not.toBeInTheDocument();
  });

  it("bypass() from one guard also carries past a sibling guard", async () => {
    const user = userEvent.setup();
    renderApp(h(TwoEditors, { dirtyA: true, dirtyB: true }));

    // bypass is a process-wide suspension by design (a page deleting the record
    // it is editing shouldn't be re-prompted by a nested guard).
    await user.click(screen.getByRole("button", { name: "Leave via bypass A" }));

    expect(await screen.findByText("OTHER PAGE")).toBeInTheDocument();
    expect(dialog()).not.toBeInTheDocument();
  });
});
