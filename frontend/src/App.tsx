import { Navigate, Route, Routes, useLocation } from "react-router";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { getLanding } from "./lib/prefs";
import AssistantPage from "./pages/AssistantPage";
import CheckDetailPage from "./pages/CheckDetailPage";
import ChecksPage from "./pages/ChecksPage";
import ConnectionBrowsePage from "./pages/ConnectionBrowsePage";
import ConnectionDetailPage from "./pages/ConnectionDetailPage";
import ConnectionsPage from "./pages/ConnectionsPage";
import CustomDashboardPage from "./pages/CustomDashboardPage";
import DashboardsListPage from "./pages/DashboardsListPage";
import DatasetDetailPage from "./pages/DatasetDetailPage";
import DatasetsPage from "./pages/DatasetsPage";
import ExceptionsPage from "./pages/ExceptionsPage";
import HomePage from "./pages/HomePage";
import IncidentsPage from "./pages/IncidentsPage";
import LineagePage from "./pages/LineagePage";
import LoginPage from "./pages/LoginPage";
import MyWorkPage from "./pages/MyWorkPage";
import ReliabilityPage from "./pages/ReliabilityPage";
import RunDetailPage from "./pages/RunDetailPage";
import RunsPage from "./pages/RunsPage";
import SettingsPage from "./pages/SettingsPage";
import WorkbenchPage from "./pages/WorkbenchPage";

const SESSION_LANDED_KEY = "dq_landed";

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
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<Layout />}>
        <Route
          path="/"
          element={
            <LandingRedirect>
              <HomePage />
            </LandingRedirect>
          }
        />
        <Route path="/my-work" element={<MyWorkPage />} />
        <Route path="/dashboards" element={<DashboardsListPage />} />
        <Route path="/dashboards/:id" element={<CustomDashboardPage />} />
        <Route path="/connections" element={<ConnectionsPage />} />
        <Route path="/connections/:id" element={<ConnectionDetailPage />} />
        <Route path="/connections/:id/browse" element={<ConnectionBrowsePage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/:id" element={<DatasetDetailPage />} />
        <Route path="/datasets/:id/:tab" element={<DatasetDetailPage />} />
        <Route path="/checks" element={<ChecksPage />} />
        <Route path="/checks/:id" element={<CheckDetailPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/:id" element={<RunDetailPage />} />
        <Route path="/exceptions" element={<ExceptionsPage />} />
        <Route path="/incidents" element={<IncidentsPage />} />
        <Route path="/reliability" element={<ReliabilityPage />} />
        <Route path="/workbench" element={<WorkbenchPage />} />
        <Route path="/lineage" element={<LineagePage />} />
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
