import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { AlertDetailPage } from "./pages/AlertDetailPage";
import { AlertsPage } from "./pages/AlertsPage";
import { GlossaryPage } from "./pages/GlossaryPage";
import { LoginPage } from "./pages/LoginPage";
import { TasksPage } from "./pages/TasksPage";
import { WatchlistPage } from "./pages/WatchlistPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route path="/" element={<AlertsPage />} />
          <Route path="/alerts/:accessionNo" element={<AlertDetailPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/glossary" element={<GlossaryPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
