import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { DashboardProvider, useDashboard } from "./layout/DashboardContext";
import { AgentPage } from "./pages/AgentPage";
import { HistoryPage } from "./pages/HistoryPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TradePage } from "./pages/TradePage";

function ShellRoutes() {
  const { sseStatus, updated, status } = useDashboard();
  return (
    <Routes>
      <Route
        element={
          <AppShell
            sseStatus={sseStatus}
            updated={updated}
            mode={status?.mode}
            enabled={status?.enabled}
          />
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="trade" element={<TradePage />} />
        <Route path="agent" element={<AgentPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  // basename empty: FastAPI serves SPA at /; HashRouter not needed if SPA fallback works.
  // Use BrowserRouter so URLs match /agent, /trade, etc.
  return (
    <BrowserRouter>
      <DashboardProvider>
        <ShellRoutes />
      </DashboardProvider>
    </BrowserRouter>
  );
}
