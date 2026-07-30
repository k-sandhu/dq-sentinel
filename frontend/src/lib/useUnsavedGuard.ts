// Unsaved-changes guard for in-app navigation (#284).
//
// Editors that hold analyst typing (dashboard builder, table knowledge, data
// contract) used to guard only `beforeunload` — i.e. closing the browser tab.
// Any *in-app* navigation (sidebar link, breadcrumb, global-search hit) unmounted
// the page and threw the draft away silently, which violates the standing bar
// that analyst state is never destroyed without asking.
//
// WHY THIS DOESN'T CALL react-router's `useBlocker`
// -------------------------------------------------
// `useBlocker` is built on `useDataRouterContext`, so outside
// a `createBrowserRouter`/`RouterProvider` tree it throws
//   "useBlocker must be used within a data router."
// This app mounts a plain `<BrowserRouter>` (src/main.tsx), so calling `useBlocker`
// here would crash every editor page instead of guarding it (verified against the
// installed package). Until the root router is migrated to a data router we guard
// the one choke point that BOTH `<Link>` clicks and `useNavigate()` funnel through:
// the `navigator` (history) object carried by react-router's navigation context.
// The interception is installed lazily on the first mounted guard, shared by all of
// them, and fully removed when the last one unmounts, so the navigator is left
// exactly as we found it. It works unchanged under a data router too (its navigator
// has the same push/replace/go shape), so migrating later is a drop-in.
//
// KNOWN LIMITATION: browser Back/Forward (popstate) is not intercepted — a
// non-data router exposes no cancellable hook for it. `beforeunload` still covers
// tab close / reload, exactly as before.
import { useCallback, useContext, useEffect, useMemo, useRef } from "react";
import type { ContextType } from "react";
import { UNSAFE_NavigationContext } from "react-router";
import { useConfirm } from "../components/confirm";

type NavigationContextValue = ContextType<typeof UNSAFE_NavigationContext>;
type RouterNavigator = NavigationContextValue["navigator"];

interface Guard {
  /** True while this guard wants to stop a navigation. */
  isBlocking: () => boolean;
  /** Ask the user. Must call `decide` exactly once. */
  prompt: (decide: (proceed: boolean) => void) => void;
}

interface Patch {
  /** The navigator's own methods, captured before we replaced them. */
  push: RouterNavigator["push"];
  replace: RouterNavigator["replace"];
  go: RouterNavigator["go"];
  guards: Set<Guard>;
  /** The guard whose prompt is currently on screen, if any. */
  asking: Guard | null;
}

const patches = new WeakMap<RouterNavigator, Patch>();

/** > 0 while a caller deliberately navigates past its own guard (see `bypass`). */
let bypassDepth = 0;

function intercept(patch: Patch, run: () => void): void {
  if (bypassDepth > 0) {
    run();
    return;
  }
  // A prompt is already on screen: swallow the extra navigation rather than stack
  // dialogs (the shared ConfirmProvider only renders one at a time).
  if (patch.asking) return;
  let blocking: Guard | null = null;
  for (const guard of patch.guards) {
    if (guard.isBlocking()) {
      blocking = guard;
      break;
    }
  }
  if (!blocking) {
    run();
    return;
  }
  const asking = blocking;
  patch.asking = asking;
  asking.prompt((proceed) => {
    // Always release the door, whichever way the user answered — a guard that
    // forgets to clear this would block navigation forever, which is worse than
    // the bug it fixes.
    if (patch.asking === asking) patch.asking = null;
    if (proceed) run();
  });
}

/** Install (or join) the interception on `navigator`; returns an unregister fn. */
function register(navigator: RouterNavigator, guard: Guard): () => void {
  let patch = patches.get(navigator);
  if (!patch) {
    const installed: Patch = {
      push: navigator.push,
      replace: navigator.replace,
      go: navigator.go,
      guards: new Set(),
      asking: null,
    };
    patches.set(navigator, installed);
    navigator.push = (...args: Parameters<RouterNavigator["push"]>) =>
      intercept(installed, () => installed.push.apply(navigator, args));
    navigator.replace = (...args: Parameters<RouterNavigator["replace"]>) =>
      intercept(installed, () => installed.replace.apply(navigator, args));
    navigator.go = (...args: Parameters<RouterNavigator["go"]>) =>
      intercept(installed, () => installed.go.apply(navigator, args));
    patch = installed;
  }
  const owner = patch;
  owner.guards.add(guard);
  return () => {
    owner.guards.delete(guard);
    // Never let an unmount strand a half-answered prompt holding the door shut.
    if (owner.asking === guard) owner.asking = null;
    if (owner.guards.size === 0 && patches.get(navigator) === owner) {
      navigator.push = owner.push;
      navigator.replace = owner.replace;
      navigator.go = owner.go;
      patches.delete(navigator);
    }
  };
}

export interface UnsavedGuard {
  /**
   * Run `fn` with the guard suspended, for a navigation the user has already
   * agreed to — e.g. leaving after deleting the very record being edited, where
   * "save your changes?" would be nonsense. Synchronous: the suspension lasts
   * exactly as long as `fn` runs.
   */
  bypass: <T>(fn: () => T) => T;
}

const DEFAULT_MESSAGE = "You have unsaved changes. If you leave this page they will be discarded.";

/**
 * Guard unsaved editor state against both in-app navigation and tab close.
 *
 * No-ops entirely while `isDirty` is false. When dirty, an in-app navigation is
 * held back and the shared confirm dialog (ConfirmProvider, mounted above the
 * router in main.tsx) asks first: confirming replays the navigation, cancelling
 * leaves the user exactly where they were and re-arms the guard for next time.
 */
export function useUnsavedGuard(isDirty: boolean, message?: string): UnsavedGuard {
  const confirm = useConfirm();
  const navigation = useContext(UNSAFE_NavigationContext) as NavigationContextValue | undefined;
  const navigator = navigation?.navigator;

  // Read through refs so the interception never has to be torn down and rebuilt
  // as the user types (and so a stale closure can't report an old dirty state).
  const dirtyRef = useRef(isDirty);
  const messageRef = useRef(message);
  const confirmRef = useRef(confirm);
  useEffect(() => {
    dirtyRef.current = isDirty;
  }, [isDirty]);
  useEffect(() => {
    messageRef.current = message;
  }, [message]);
  useEffect(() => {
    confirmRef.current = confirm;
  }, [confirm]);

  // Tab close / reload. The browser shows its own generic prompt here; custom
  // text has been ignored by every major browser for years.
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  useEffect(() => {
    if (!navigator) return;
    // Flipped on unmount so a prompt that outlives this component can't claim to
    // still be blocking.
    let live = true;
    const guard: Guard = {
      isBlocking: () => live && dirtyRef.current,
      prompt: (decide) => {
        let settled = false;
        const done = (proceed: boolean) => {
          if (settled) return;
          settled = true;
          decide(proceed);
        };
        Promise.resolve(
          confirmRef.current({
            title: "Discard unsaved changes?",
            body: messageRef.current ?? DEFAULT_MESSAGE,
            confirmLabel: "Discard changes",
            cancelLabel: "Keep editing",
            danger: true,
          }),
        ).then(done, () => done(false));
      },
    };
    const unregister = register(navigator, guard);
    return () => {
      live = false;
      unregister();
    };
  }, [navigator]);

  const bypass = useCallback(<T,>(fn: () => T): T => {
    bypassDepth += 1;
    try {
      return fn();
    } finally {
      bypassDepth -= 1;
    }
  }, []);

  return useMemo(() => ({ bypass }), [bypass]);
}
