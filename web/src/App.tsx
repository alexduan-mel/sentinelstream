import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { AlertsPage } from "./pages/AlertsPage";
import { GlossaryPage } from "./pages/GlossaryPage";
import { JournalPage } from "./pages/JournalPage";
import { LogDecisionPage } from "./pages/LogDecisionPage";
import { LoginPage } from "./pages/LoginPage";
import { TasksPage } from "./pages/TasksPage";
import { TickerPage } from "./pages/TickerPage";
import { WatchlistPage } from "./pages/WatchlistPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          {/* One page for the list and an open filing, so the list stays loaded while one is read. */}
          <Route element={<AlertsPage />}>
            <Route path="/" element={null} />
            <Route path="/alerts/:accessionNo" element={null} />
          </Route>
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/watchlist/:symbol" element={<TickerPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="/journal/new" element={<LogDecisionPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/glossary" element={<GlossaryPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
