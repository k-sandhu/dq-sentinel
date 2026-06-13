import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import type { ConnectionHealth, Dataset } from "../api/types";
import { useAuth } from "../auth";
import { FAVORITES_SIDEBAR_CAP, getFavorites, pruneStalePrefs, subscribePrefs } from "../lib/prefs";
import ErrorBoundary from "./ErrorBoundary";
import { Icon } from "./ui";

const NAV_GROUPS: {
  label: string;
  items: { to: string; label: string; icon: string; end?: boolean }[];
}[] = [
  { label: "Overview", items: [{ to: "/", label: "Home", icon: "home", end: true }] },
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

/** Global dataset search: debounced GET /datasets?q=…, top 8 hits, "/" focuses. */
function GlobalSearch() {
  const navigate = useNavigate();
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(term.trim()), 250);
    return () => clearTimeout(t);
  }, [term]);

  const { data: hits } = useQuery({
    queryKey: ["dataset-search", debounced],
    queryFn: () => api.get<Dataset[]>(`/datasets?q=${encodeURIComponent(debounced)}`),
    enabled: debounced.length > 0,
    staleTime: 30_000,
    placeholderData: (prev) => prev, // keep last results while typing — no dropdown flicker
  });

  // "/" focuses the search box unless the user is already typing somewhere.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      e.preventDefault();
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

  const top = (hits ?? []).slice(0, 8);
  const visible = open && debounced.length > 0 && hits !== undefined;

  function go(id: number) {
    setOpen(false);
    setTerm("");
    setDebounced("");
    navigate(`/datasets/${id}`);
  }

  return (
    <div className="global-search" ref={boxRef}>
      <Icon name="search" size={15} />
      <input
        ref={inputRef}
        type="text"
        placeholder="Search datasets…"
        aria-label="Search datasets"
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
          } else if (e.key === "Enter" && visible && top.length > 0) {
            go(top[0].id);
          }
        }}
      />
      {!term && <span className="kbd search-kbd">/</span>}
      {visible && (
        <div className="search-pop">
          {top.length === 0 ? (
            <div className="search-empty">No datasets match “{debounced}”</div>
          ) : (
            top.map((d) => (
              <button key={d.id} type="button" className="search-hit" onClick={() => go(d.id)}>
                <span className="title">
                  {d.schema_name ? `${d.schema_name}.` : ""}
                  {d.table_name}
                </span>
                <span className="meta">{d.connection_name}</span>
              </button>
            ))
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
        <div className="logo">
          <span className="logo-mark">
            <Icon name="shield" size={16} />
          </span>
          DQ Sentinel
        </div>
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
