import { useLayoutEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { getLandingPath, hasLandedThisSession, markLandedThisSession } from "./lib/prefs";
import AssistantPage from "./pages/AssistantPage";
import CheckDetailPage from "./pages/CheckDetailPage";
import ChecksPage from "./pages/ChecksPage";
import ConnectionBrowsePage from "./pages/ConnectionBrowsePage";
import ConnectionDetailPage from "./pages/ConnectionDetailPage";
import ConnectionsPage from "./pages/ConnectionsPage";
import DatasetDetailPage from "./pages/DatasetDetailPage";
import DatasetsPage from "./pages/DatasetsPage";
import ExceptionsPage from "./pages/ExceptionsPage";
import HomePage from "./pages/HomePage";
import LineagePage from "./pages/LineagePage";
import LoginPage from "./pages/LoginPage";
import RunDetailPage from "./pages/RunDetailPage";
import RunsPage from "./pages/RunsPage";
import SettingsPage from "./pages/SettingsPage";
import WorkbenchPage from "./pages/WorkbenchPage";

function LandingRedirect() {
  const location = useLocation();
  const [landingTarget, setLandingTarget] = useState<string | null>(null);

  useLayoutEffect((): void => {
    if (hasLandedThisSession()) return;
    markLandedThisSession();
    if (location.pathname !== "/" || location.search || location.hash) return;

    const landing = getLandingPath();
    if (landing !== "/") setLandingTarget(landing);
  }, [location.hash, location.pathname, location.search]);

  return landingTarget ? <Navigate to={landingTarget} replace /> : <HomePage />;
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
        <Route path="/" element={<LandingRedirect />} />
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
        <Route path="/workbench" element={<WorkbenchPage />} />
        <Route path="/lineage" element={<LineagePage />} />
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
