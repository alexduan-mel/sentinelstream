import { Layout, Spin } from "antd";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { LoadError } from "../components/LoadError";
import { SettingsGate } from "../components/SettingsGate";
import { TopBar } from "../components/TopBar";
import { useAuth } from "./AuthContext";

/** The signed-in shell: top bar plus the page. Signed-out visitors go to /login and come back after. */
export function RequireAuth() {
  const { state, retry } = useAuth();
  const location = useLocation();

  if (state.status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spin />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="p-24">
        <LoadError message={state.message} onRetry={retry} />
      </div>
    );
  }
  if (state.user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return (
    <SettingsGate>
      <Layout className="min-h-screen">
        <TopBar username={state.user.username} />
        <Layout.Content className="p-24">
          <Outlet />
        </Layout.Content>
      </Layout>
    </SettingsGate>
  );
}
