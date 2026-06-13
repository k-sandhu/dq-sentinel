import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import type { ConnectionHealth, Dataset, SearchHit, SearchOut } from "../api/types";
import { useAuth } from "../auth";
import { getFavoriteDatasetIds, getRecentDatasets, pruneDatasetPrefs, subscribePrefs } from "../lib/prefs";
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

const SEARCH_TYPE_LABELS: Record<SearchHit["type"], string> = {
  dataset: "Datasets",
  check: "Checks",
  connection: "Connections",
  saved_query: "Saved queries",
};
const SEARCH_TYPE_ORDER: SearchHit["type"][] = ["dataset", "check", "connection", "saved_query"];

function datasetTitle(dataset: Dataset) {
  return `${dataset.schema_name ? `${dataset.schema_name}.` : ""}${dataset.table_name}`;
}

function favoriteDatasetLabel(dataset: Dataset): string {
  return dataset.display_name || datasetTitle(dataset);
}

function SidebarNavGroup({ group }: { group: (typeof NAV_GROUPS)[number] }) {
  return (
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
  );
}

function FavoriteDatasetsNav() {
  const [favorites, setFavorites] = useState<number[]>(() => getFavoriteDatasetIds());
  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  useEffect(() => subscribePrefs(() => setFavorites(getFavoriteDatasetIds())), []);

  useEffect(() => {
    if (!datasets) return;
    setFavorites(pruneDatasetPrefs(datasets.map((dataset) => dataset.id)).favorites);
  }, [datasets]);

  const favoriteDatasets = useMemo(() => {
    if (!datasets) return [];
    const datasetsById = new Map(datasets.map((dataset) => [dataset.id, dataset]));
    return favorites
      .map((id) => datasetsById.get(id))
      .filter((dataset): dataset is Dataset => Boolean(dataset))
      .slice(0, 6);
  }, [datasets, favorites]);

  if (favoriteDatasets.length === 0) return null;

  return (
    <div className="nav-group favorites-nav">
      <div className="nav-section">Favorites</div>
      <nav>
        {favoriteDatasets.map((dataset) => (
          <NavLink
            key={dataset.id}
            to={`/datasets/${dataset.id}`}
            className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
            title={`${favoriteDatasetLabel(dataset)} on ${dataset.connection_name}`}
          >
            <Icon name="star-filled" />
            <span className="nav-label">{favoriteDatasetLabel(dataset)}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

/** Global entity search: debounced GET /search?q=…, grouped dropdown, "/" and Ctrl/Cmd+K focus. */
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

  const recentIds = term.trim() ? [] : getRecentDatasets().map((recent) => recent.id);
  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
    enabled: open && debounced.length === 0 && recentIds.length > 0,
    staleTime: 30_000,
  });
  const recentHits = useMemo<SearchHit[]>(() => {
    if (debounced.length > 0 || !datasets) return [];
    const byId = new Map(datasets.map((dataset) => [dataset.id, dataset]));
    return recentIds
      .map((id) => byId.get(id))
      .filter((dataset): dataset is Dataset => Boolean(dataset))
      .map((dataset) => ({
        type: "dataset",
        id: dataset.id,
        title: datasetTitle(dataset),
        subtitle: `Recently viewed - ${dataset.connection_name}`,
        url: `/datasets/${dataset.id}`,
      }));
  }, [datasets, debounced.length, recentIds]);

  const hits = debounced.length > 0 ? data?.hits ?? [] : recentHits;
  const visible =
    open &&
    ((debounced.length > 0 && data !== undefined) || (debounced.length === 0 && recentHits.length > 0));
  const currentActive = hits.length > 0 ? Math.min(activeIndex, hits.length - 1) : 0;
  const groupedHits = SEARCH_TYPE_ORDER.map((type) => ({
    type,
    hits: hits.map((hit, index) => ({ hit, index })).filter((item) => item.hit.type === type),
  })).filter((group) => group.hits.length > 0);

  useEffect(() => {
    setActiveIndex(0);
  }, [debounced, hits.length]);

  useEffect(() => {
    if (hits.length > 0 && activeIndex >= hits.length) setActiveIndex(hits.length - 1);
  }, [activeIndex, hits.length]);

  // "/" and Ctrl/Cmd+K focus the search box unless the user is already typing somewhere.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const slash = e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey;
      const commandK = e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey) && !e.altKey;
      if (!slash && !commandK) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      e.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
      setOpen(true);
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
    setActiveIndex(0);
    navigate(url);
  }

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
          } else if (e.key === "ArrowDown" && visible && hits.length > 0) {
            e.preventDefault();
            setActiveIndex((index) => (index + 1) % hits.length);
          } else if (e.key === "ArrowUp" && visible && hits.length > 0) {
            e.preventDefault();
            setActiveIndex((index) => (index - 1 + hits.length) % hits.length);
          } else if (e.key === "Enter" && visible && hits.length > 0) {
            e.preventDefault();
            go(hits[currentActive].url);
          }
        }}
      />
      {!term && <span className="kbd search-kbd">Ctrl K</span>}
      {visible && (
        <div className="search-pop">
          {hits.length === 0 ? (
            <div className="search-empty">No results match “{debounced}”</div>
          ) : (
            groupedHits.map((group) => (
              <div className="search-group" key={group.type}>
                <div className="search-group-title">
                  {debounced.length === 0 && group.type === "dataset" ? "Recently viewed" : SEARCH_TYPE_LABELS[group.type]}
                </div>
                {group.hits.map(({ hit, index }) => (
                  <button
                    key={`${hit.type}-${hit.id}`}
                    type="button"
                    className={`search-hit${index === currentActive ? " active" : ""}`}
                    onClick={() => go(hit.url)}
                    onMouseEnter={() => setActiveIndex(index)}
                  >
                    <span className="title">{hit.title}</span>
                    <span className="meta">{hit.subtitle}</span>
                  </button>
                ))}
              </div>
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
      /* storage unavailable - density still applies for this session */
    }
    setDensity(next);
  }
  return (
    <button
      type="button"
      className="small icon-only"
      onClick={toggle}
      title={density === "compact" ? "Use comfortable density" : "Use compact density"}
      aria-label="Toggle density"
    >
      <Icon name="rows" size={14} />
    </button>
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
        <SidebarNavGroup group={NAV_GROUPS[0]} />
        <FavoriteDatasetsNav />
        {NAV_GROUPS.slice(1).map((group) => (
          <SidebarNavGroup key={group.label} group={group} />
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
