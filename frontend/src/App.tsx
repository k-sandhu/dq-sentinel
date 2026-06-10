import { Navigate, Route, Routes } from "react-router";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import ChecksPage from "./pages/ChecksPage";
import ConnectionBrowsePage from "./pages/ConnectionBrowsePage";
import ConnectionsPage from "./pages/ConnectionsPage";
import DatasetDetailPage from "./pages/DatasetDetailPage";
import DatasetsPage from "./pages/DatasetsPage";
import ExceptionsPage from "./pages/ExceptionsPage";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import RunsPage from "./pages/RunsPage";
import SettingsPage from "./pages/SettingsPage";
import WorkbenchPage from "./pages/WorkbenchPage";

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
        <Route path="/" element={<HomePage />} />
        <Route path="/connections" element={<ConnectionsPage />} />
        <Route path="/connections/:id/browse" element={<ConnectionBrowsePage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/:id" element={<DatasetDetailPage />} />
        <Route path="/datasets/:id/:tab" element={<DatasetDetailPage />} />
        <Route path="/checks" element={<ChecksPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/exceptions" element={<ExceptionsPage />} />
        <Route path="/workbench" element={<WorkbenchPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
