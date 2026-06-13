import { Navigate, Route, Routes } from "react-router";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { PREF_KEYS, getPref } from "./lib/prefs";
import AssistantPage from "./pages/AssistantPage";
import ChecksPage from "./pages/ChecksPage";
import ConnectionBrowsePage from "./pages/ConnectionBrowsePage";
import ConnectionsPage from "./pages/ConnectionsPage";
import CustomDashboardPage from "./pages/CustomDashboardPage";
import DashboardsListPage from "./pages/DashboardsListPage";
import DatasetDetailPage from "./pages/DatasetDetailPage";
import DatasetsPage from "./pages/DatasetsPage";
import ExceptionsPage from "./pages/ExceptionsPage";
import HomePage from "./pages/HomePage";
import LineagePage from "./pages/LineagePage";
import LoginPage from "./pages/LoginPage";
import RunsPage from "./pages/RunsPage";
import SettingsPage from "./pages/SettingsPage";
import WorkbenchPage from "./pages/WorkbenchPage";

/** Honour the user's "default landing page" preference (#59/#68) on the "/" route:
 *  redirect once to the saved path when it points somewhere other than home. */
function HomeOrLanding() {
  const landing = getPref(PREF_KEYS.landing);
  if (landing && landing !== "/") return <Navigate to={landing} replace />;
  return <HomePage />;
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
        <Route path="/" element={<HomeOrLanding />} />
        <Route path="/dashboards" element={<DashboardsListPage />} />
        <Route path="/dashboards/:id" element={<CustomDashboardPage />} />
        <Route path="/connections" element={<ConnectionsPage />} />
        <Route path="/connections/:id/browse" element={<ConnectionBrowsePage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/:id" element={<DatasetDetailPage />} />
        <Route path="/datasets/:id/:tab" element={<DatasetDetailPage />} />
        <Route path="/checks" element={<ChecksPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/exceptions" element={<ExceptionsPage />} />
        <Route path="/workbench" element={<WorkbenchPage />} />
        <Route path="/lineage" element={<LineagePage />} />
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
