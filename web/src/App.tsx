import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { LoginPage } from "./pages/LoginPage";
import { TasksPage } from "./pages/TasksPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route path="/tasks" element={<TasksPage />} />
        </Route>
        {/* Alerts takes "/" in M1; until then it opens Tasks. */}
        <Route path="*" element={<Navigate to="/tasks" replace />} />
      </Routes>
    </AuthProvider>
  );
}
