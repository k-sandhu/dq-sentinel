import { lazy, Suspense } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router";
import { useAuth } from "./auth";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { resolvePostLoginTarget } from "./lib/postLogin";
import { getLanding } from "./lib/prefs";
import CatalogPage from "./pages/CatalogPage";
import ChecksPage from "./pages/ChecksPage";
import ConnectionBrowsePage from "./pages/ConnectionBrowsePage";
import ConnectionDetailPage from "./pages/ConnectionDetailPage";
import ConnectionsPage from "./pages/ConnectionsPage";
import DashboardsListPage from "./pages/DashboardsListPage";
import DatasetsPage from "./pages/DatasetsPage";
import ExceptionsPage from "./pages/ExceptionsPage";
import FeaturesPage from "./pages/FeaturesPage";
import IncidentsPage from "./pages/IncidentsPage";
import LoginPage from "./pages/LoginPage";
import MyWorkPage from "./pages/MyWorkPage";
import ReliabilityPage from "./pages/ReliabilityPage";
import RunDetailPage from "./pages/RunDetailPage";
import RunsPage from "./pages/RunsPage";
import StatusPage from "./pages/StatusPage";

/*
 * Route-level code-splitting (FE-1, extended in #313).
 *
 * A static import from this file lands in the *entry* chunk, so it is paid for
 * on the first paint of every session regardless of where the analyst is going.
 * Every page below either drags in a large third-party stack or is a large page
 * in its own right, and each one is measurably absent from most sessions:
 *
 *   HomePage, CheckDetailPage      recharts        (vendor-charts, 446 kB)
 *   CustomDashboardPage            recharts + react-markdown
 *   DatasetDetailPage              @xyflow + dagre (LineageTab), react-markdown
 *                                  (RcaTab), recharts (DashboardsTab)
 *   DocsPage                       react-markdown  (~350 kB of micromark/mdast)
 *   SettingsPage                   ~59 kB of admin screens most users never open
 *   WorkbenchPage                  CodeMirror + sql-formatter + react-table
 *   LineagePage                    @xyflow + dagre
 *   AssistantPage                  recharts (inline PanelChart) + react-markdown
 *
 * These are *all* the importers of recharts, react-markdown and @xyflow, which
 * is what takes those three stacks off the critical path rather than merely
 * moving them around. Cost: one extra request the first time a session opens
 * such a route, behind the same Spinner the shell already uses while auth
 * resolves. Everything still statically imported above is a small list/detail
 * page with no heavy dependency — keeping those eager keeps the routes analysts
 * bounce between all day instant.
 */
const AssistantPage = lazy(() => import("./pages/AssistantPage"));
const CheckDetailPage = lazy(() => import("./pages/CheckDetailPage"));
const CustomDashboardPage = lazy(() => import("./pages/CustomDashboardPage"));
const DatasetDetailPage = lazy(() => import("./pages/DatasetDetailPage"));
const DocsPage = lazy(() => import("./pages/DocsPage"));
const HomePage = lazy(() => import("./pages/HomePage"));
const LineagePage = lazy(() => import("./pages/LineagePage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const WorkbenchPage = lazy(() => import("./pages/WorkbenchPage"));

const SESSION_LANDED_KEY = "dq_landed";

/**
 * Deep-link preservation through login (UX benchmark P1). Logged-out visits to
 * any app URL redirect to /login carrying the intended location in router
 * state; after auth, PostLoginRedirect sends the user there instead of "/".
 * The target is validated by safeInternalPath so it can't become an open
 * redirect. State survives the auth re-render because it lives on the /login
 * history entry itself.
 */
function LoginRedirect() {
  const location = useLocation();
  const from = location.pathname + location.search + location.hash;
  return <Navigate to="/login" replace state={{ from }} />;
}

/**
 * Shared Suspense boundary for the lazy route chunks (#313). It reuses the same
 * `Spinner` the shell shows while auth resolves, so a route chunk that arrives a
 * beat late reads as "loading", not as a blank pane. Used as a pathless layout
 * route so one boundary covers every lazy route in the group.
 */
/** Chrome-less lazy routes (/docs). These render OUTSIDE `Layout`, so they do
 *  not inherit its per-route `ErrorBoundary` — without one here, a failed chunk
 *  fetch (the normal outcome of navigating after a deploy replaced the hashed
 *  assets) propagates to the root and unmounts the whole SPA, leaving a blank
 *  page that no in-app navigation can recover. Keyed by pathname to match
 *  Layout, so moving to another route clears a previous route's error. */
function LazyRoutes() {
  const location = useLocation();
  return (
    <ErrorBoundary key={location.pathname}>
      <Suspense fallback={<Spinner label="Loading…" />}>
        <Outlet />
      </Suspense>
    </ErrorBoundary>
  );
}

function PostLoginRedirect() {
  const location = useLocation();
  const from = (location.state as { from?: unknown } | null)?.from;
  return <Navigate to={resolvePostLoginTarget(from, location.search)} replace />;
}

/**
 * Default-landing redirect (#59) wrapping the index route. On the FIRST visit to
 * "/" per browser session it sends the user to their configured landing page;
 * after that the session guard lets explicit "Home" navigation through. It never
 * traps the user:
 *   - deep links win — any query (or non-"/" path) bypasses the redirect entirely,
 *     so notification-email / Slack links land where they point;
 *   - the redirect is `replace`, so Back doesn't bounce off "/".
 */
function LandingRedirect({ children }: { children: React.ReactNode }) {
  const location = useLocation();

  // Deep-link bypass: a query string (or any path beyond "/") always wins over
  // the landing preference. Evaluate before touching the session guard so a deep
  // link doesn't consume the once-per-session redirect.
  const isBareRoot = location.pathname === "/" && location.search === "";
  if (!isBareRoot) return <>{children}</>;

  let landed = false;
  try {
    landed = sessionStorage.getItem(SESSION_LANDED_KEY) === "1";
    sessionStorage.setItem(SESSION_LANDED_KEY, "1");
  } catch {
    // sessionStorage unavailable — treat as "already landed" so we never loop.
    landed = true;
  }

  const landing = getLanding();
  if (!landed && landing !== "/") return <Navigate to={landing} replace />;
  return <>{children}</>;
}

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Starting DQ Sentinel…" />;

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<LoginRedirect />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<PostLoginRedirect />} />
      {/* Standalone reference pages — separate from the main app shell (no
          sidebar), reachable via the floating launcher. Static paths outrank
          the Layout group's "*" catch-all. */}
      <Route element={<LazyRoutes />}>
        <Route path="/docs" element={<DocsPage />} />
        <Route path="/docs/:slug" element={<DocsPage />} />
      </Route>
      <Route path="/features" element={<FeaturesPage />} />
      <Route element={<Layout />}>
        <Route path="/my-work" element={<MyWorkPage />} />
        <Route path="/dashboards" element={<DashboardsListPage />} />
        <Route path="/catalog" element={<CatalogPage />} />
        <Route path="/connections" element={<ConnectionsPage />} />
        <Route path="/connections/:id" element={<ConnectionDetailPage />} />
        <Route path="/connections/:id/browse" element={<ConnectionBrowsePage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/checks" element={<ChecksPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/:id" element={<RunDetailPage />} />
        <Route path="/exceptions" element={<ExceptionsPage />} />
        <Route path="/incidents" element={<IncidentsPage />} />
        <Route path="/reliability" element={<ReliabilityPage />} />
        <Route path="/status" element={<StatusPage />} />
        {/* Lazy routes (#313). Path ranking, not source order, decides which
            route matches, so grouping them under one Suspense boundary is
            purely about sharing the fallback. */}
        <Route element={<LazyRoutes />}>
          <Route
            path="/"
            element={
              <LandingRedirect>
                <HomePage />
              </LandingRedirect>
            }
          />
          <Route path="/dashboards/:id" element={<CustomDashboardPage />} />
          <Route path="/datasets/:id" element={<DatasetDetailPage />} />
          <Route path="/datasets/:id/:tab" element={<DatasetDetailPage />} />
          <Route path="/checks/:id" element={<CheckDetailPage />} />
          <Route path="/workbench" element={<WorkbenchPage />} />
          <Route path="/lineage" element={<LineagePage />} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
