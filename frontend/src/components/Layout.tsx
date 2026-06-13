import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import type { ConnectionHealth, Dataset, SearchHit, SearchOut } from "../api/types";
import { useAuth } from "../auth";
import { FAVORITES_SIDEBAR_CAP, getFavorites, pruneStalePrefs, subscribePrefs } from "../lib/prefs";
import ErrorBoundary from "./ErrorBoundary";
import { Icon } from "./ui";

const NAV_GROUPS: {
  label: string;
  items: { to: string; label: string; icon: string; end?: boolean }[];
}[] = [
  {
    label: "Overview",
    items: [
      { to: "/", label: "Home", icon: "home", end: true },
      { to: "/dashboards", label: "Dashboards", icon: "grid" },
    ],
  },
  {
    label: "Sources",
    items: [
      { to: "/connections", label: "Connections", icon: "db" },
      { to: "/datasets", label: "Datasets", icon: "table" },
    ],
  },
  {
    label: "Quality",
    items: [
      { to: "/checks", label: "Checks", icon: "shield" },
      { to: "/runs", label: "Runs", icon: "play" },
      { to: "/exceptions", label: "Exceptions", icon: "alert" },
    ],
  },
  {
    label: "Explore",
    items: [
      { to: "/workbench", label: "Workbench", icon: "search" },
      { to: "/lineage", label: "Lineage", icon: "graph" },
      { to: "/assistant", label: "Assistant", icon: "chat" },
    ],
  },
];

/** Fleet-health pill: polls GET /connections/health once a minute (shares the
 *  "fleet-health" cache key with ConnectionsPage's on-demand probe). */
function FleetHealthPill() {
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["fleet-health"],
    queryFn: () => api.get<ConnectionHealth[]>("/connections/health"),
    refetchInterval: 60_000,
  });

  let dot = "neutral";
  let label = "Sources";
  if (data) {
    const failing = data.filter((h) => !h.ok).length;
    if (data.length === 0) {
      label = "No sources";
    } else if (failing > 0) {
      dot = "fail";
      label = `${failing} failing`;
    } else {
      dot = "ok";
      label = "Sources healthy";
    }
  }
  return (
    <button
      type="button"
      className="environment-pill"
      onClick={() => navigate("/connections")}
      title="Connection fleet health — click to manage sources"
    >
      <span className={`env-dot ${dot}`} />
      {label}
    </button>
  );
}

// Group order + display labels for the command palette. Matches the backend's
// hit ordering in app/api/search.py.
const SEARCH_GROUPS: { type: SearchHit["type"]; label: string }[] = [
  { type: "dataset", label: "Datasets" },
  { type: "check", label: "Checks" },
  { type: "connection", label: "Connections" },
  { type: "saved_query", label: "Saved queries" },
];

/** "Recently viewed" datasets from sibling #59's lib/prefs (issue #59). That
 *  module does not exist in every build, so we feature-detect it with a
 *  variable-specifier dynamic import (keeps tsc + vite build green when absent)
 *  and skip the section silently if it or its accessor is missing. */
function useRecentDatasets(enabled: boolean): SearchHit[] {
  const [recents, setRecents] = useState<SearchHit[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    (async () => {
      try {
        // Non-literal specifier so the bundler/TS don't hard-require the module.
        const spec = "../lib/prefs";
        const mod = (await import(/* @vite-ignore */ spec)) as {
          getRecentDatasets?: () => { id: number; title?: string; subtitle?: string }[];
          getRecents?: () => { id: number; title?: string; subtitle?: string }[];
        };
        const read = mod.getRecentDatasets ?? mod.getRecents;
        const rows = read?.() ?? [];
        if (cancelled) return;
        setRecents(
          rows.slice(0, 6).map((r) => ({
            type: "dataset" as const,
            id: r.id,
            title: r.title ?? `Dataset ${r.id}`,
            subtitle: r.subtitle ?? "Recently viewed",
            url: `/datasets/${r.id}`,
          })),
        );
      } catch {
        /* #59 not present in this build — no recents section, by design */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return recents;
}

/** Global command palette: debounced GET /search?q=…, hits grouped by entity
 *  type, arrow-key navigation, Enter to jump. "/" and Ctrl/Cmd+K both focus it;
 *  empty focus shows "Recently viewed" datasets when sibling #59's prefs exist. */
function GlobalSearch() {
  const navigate = useNavigate();
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(term.trim()), 250);
    return () => clearTimeout(t);
  }, [term]);

  const { data } = useQuery({
    queryKey: ["global-search", debounced],
    queryFn: () => api.get<SearchOut>(`/search?q=${encodeURIComponent(debounced)}`),
    enabled: debounced.length > 0,
    staleTime: 30_000,
    placeholderData: (prev) => prev, // keep last results while typing — no dropdown flicker
  });

  // Empty-query "Recently viewed" (soft dep on #59); only fetched when the box
  // is open with no query typed.
  const recents = useRecentDatasets(open && debounced.length === 0);

  const hits = debounced.length > 0 ? (data?.hits ?? []) : recents;
  // Flattened hit list (group order) for keyboard navigation.
  const flat: SearchHit[] = SEARCH_GROUPS.flatMap((g) => hits.filter((h) => h.type === g.type));
  const showRecents = debounced.length === 0 && recents.length > 0;
  const visible =
    open && (showRecents || (debounced.length > 0 && data !== undefined));

  // Reset the highlight whenever the result set changes.
  useEffect(() => {
    setActiveIndex(0);
  }, [debounced, hits.length]);

  // "/" or Ctrl/Cmd+K focuses the search box unless the user is already typing.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const isSlash = e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey;
      const isCmdK = (e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K");
      if (!isSlash && !isCmdK) return;
      const el = document.activeElement as HTMLElement | null;
      // Ctrl/Cmd+K still works from inside inputs; "/" must not hijack typing.
      if (isSlash) {
        const tag = el?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable)
          return;
      }
      e.preventDefault(); // beat the browser's "/" quick-find and Cmd+K address bar
      setOpen(true);
      inputRef.current?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Click outside closes the dropdown.
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  function go(url: string) {
    setOpen(false);
    setTerm("");
    setDebounced("");
    navigate(url);
  }

  let flatCursor = -1; // running index across groups so highlight maps to `flat`
  return (
    <div className="global-search" ref={boxRef}>
      <Icon name="search" size={15} />
      <input
        ref={inputRef}
        type="text"
        placeholder="Search datasets, checks, connections…"
        aria-label="Search datasets, checks, connections"
        value={term}
        onChange={(e) => {
          setTerm(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setOpen(false);
            inputRef.current?.blur();
          } else if (e.key === "ArrowDown") {
            if (flat.length === 0) return;
            e.preventDefault();
            setActiveIndex((i) => Math.min(i + 1, flat.length - 1));
          } else if (e.key === "ArrowUp") {
            if (flat.length === 0) return;
            e.preventDefault();
            setActiveIndex((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter" && visible && flat.length > 0) {
            go(flat[Math.min(activeIndex, flat.length - 1)].url);
          }
        }}
      />
      {!term && <span className="kbd search-kbd">Ctrl K</span>}
      {visible && (
        <div className="search-pop">
          {showRecents && <div className="nav-section">Recently viewed</div>}
          {debounced.length > 0 && flat.length === 0 ? (
            <div className="search-empty">No matches for “{debounced}”</div>
          ) : (
            SEARCH_GROUPS.map((group) => {
              const groupHits = hits.filter((h) => h.type === group.type);
              if (groupHits.length === 0) return null;
              return (
                <div key={group.type}>
                  {!showRecents && <div className="nav-section">{group.label}</div>}
                  {groupHits.map((h) => {
                    flatCursor += 1;
                    const idx = flatCursor;
                    return (
                      <button
                        key={`${h.type}-${h.id}`}
                        type="button"
                        className={`search-hit${idx === activeIndex ? " active" : ""}`}
                        onMouseEnter={() => setActiveIndex(idx)}
                        onClick={() => go(h.url)}
                      >
                        <span className="title">{h.title}</span>
                        <span className="meta">{h.subtitle}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

/** Dark-mode toggle: flips html[data-theme] and persists to localStorage "dq-theme".
 *  index.html applies the stored theme before first paint. */
function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("dq-theme", next);
    } catch {
      /* storage unavailable — theme still applies for this session */
    }
    setTheme(next);
  }
  return (
    <button
      type="button"
      className="small icon-only"
      onClick={toggle}
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      aria-label="Toggle color theme"
    >
      <Icon name={theme === "dark" ? "sun" : "moon"} size={14} />
    </button>
  );
}

/** Density toggle: flips html[data-density] and persists to localStorage
 *  "dq-density". index.html applies the stored density before first paint.
 *  Personal (per-browser) setting, not a tenant/server pref — see issue #58. */
function DensityToggle() {
  const [density, setDensity] = useState<"comfortable" | "compact">(() =>
    document.documentElement.dataset.density === "compact" ? "compact" : "comfortable",
  );
  function toggle() {
    const next = density === "compact" ? "comfortable" : "compact";
    if (next === "compact") {
      document.documentElement.dataset.density = "compact";
    } else {
      delete document.documentElement.dataset.density;
    }
    try {
      localStorage.setItem("dq-density", next);
    } catch {
      /* storage unavailable — density still applies for this session */
    }
    setDensity(next);
  }
  return (
    <button
      type="button"
      className="small icon-only"
      onClick={toggle}
      title={density === "compact" ? "Switch to comfortable density" : "Switch to compact density"}
      aria-label="Toggle row density"
      aria-pressed={density === "compact"}
    >
      <Icon name="rows" size={14} />
    </button>
  );
}

/** Sidebar "Favorites" group (#59): up to FAVORITES_SIDEBAR_CAP starred datasets
 *  as NavLinks. Names resolve from the shared ["datasets"] query cache (same key
 *  the rest of the app uses — no extra request). Stays in sync with star toggles
 *  via the `dq:prefs` window event, prunes stale ids once the live list loads,
 *  and renders nothing when there are no (resolvable) favorites. */
function FavoritesNav() {
  // Re-render on in-tab pref changes (star toggled elsewhere) without a remount.
  const [favIds, setFavIds] = useState<number[]>(() => getFavorites());
  useEffect(() => subscribePrefs(() => setFavIds(getFavorites())), []);

  // Reuse the shared cache; don't trigger a fetch from the sidebar — if the list
  // isn't loaded yet we simply render nothing until a page populates it.
  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
    enabled: favIds.length > 0,
  });

  // Once we know the live ids, drop any favorites pointing at deleted datasets
  // (prunes storage too, which fires dq:prefs and refreshes favIds).
  useEffect(() => {
    if (datasets) pruneStalePrefs(datasets.map((d) => d.id));
  }, [datasets]);

  if (favIds.length === 0) return null;

  const byId = new Map((datasets ?? []).map((d) => [d.id, d]));
  // Keep starred order (most-recent first); resolve only datasets we can name.
  const items = favIds
    .map((id) => byId.get(id))
    .filter((d): d is Dataset => d !== undefined)
    .slice(0, FAVORITES_SIDEBAR_CAP);

  if (items.length === 0) return null;

  return (
    <div className="nav-group" key="Favorites">
      <div className="nav-section">Favorites</div>
      <nav>
        {items.map((d) => {
          const label = `${d.schema_name ? `${d.schema_name}.` : ""}${d.table_name}`;
          return (
            <NavLink
              key={d.id}
              to={`/datasets/${d.id}`}
              className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
              title={`${label} · ${d.connection_name}`}
            >
              <Icon name="star-filled" />
              <span className="fav-name">{label}</span>
            </NavLink>
          );
        })}
      </nav>
    </div>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  return (
    <div className="app">
      <aside className="sidebar">
        <Link to="/" className="logo" aria-label="DQ Sentinel — home">
          <span className="logo-mark">
            <Icon name="shield" size={16} />
          </span>
          DQ Sentinel
        </Link>
        {NAV_GROUPS.map((group) => (
          <Fragment key={group.label}>
            <div className="nav-group">
              <div className="nav-section">{group.label}</div>
              <nav>
                {group.items.map((n) => (
                  <NavLink
                    key={n.to}
                    to={n.to}
                    end={n.end}
                    className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
                  >
                    <Icon name={n.icon} />
                    {n.label}
                  </NavLink>
                ))}
              </nav>
            </div>
            {/* Favorites group sits between "Overview" and "Sources" (#59). */}
            {group.label === "Overview" && <FavoritesNav />}
          </Fragment>
        ))}
        <div className="spacer" />
        <nav>
          <NavLink to="/settings" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <Icon name="settings" />
            Settings
          </NavLink>
        </nav>
        <div className="user-box">
          <div className="email" title={user?.email}>
            {user?.name || user?.email}
          </div>
          <div style={{ color: "var(--text-light)" }}>{user?.role}</div>
          <button className="small ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">
        <div className="topbar">
          <FleetHealthPill />
          <GlobalSearch />
          <div className="topbar-actions">
            <DensityToggle />
            <ThemeToggle />
          </div>
        </div>
        {/* keyed by path so a crashed page resets when the user navigates away */}
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
