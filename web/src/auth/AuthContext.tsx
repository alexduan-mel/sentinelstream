import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from "react";

import { fetchCurrentUser, login as apiLogin, logout as apiLogout, type User } from "../api/auth";
import { ApiError, onUnauthorized } from "../api/client";

type AuthState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; user: User | null };

type AuthContextValue = {
  state: AuthState;
  signIn: (username: string, password: string) => Promise<User>;
  signOut: () => Promise<void>;
  retry: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  const load = useCallback(() => {
    setState({ status: "loading" });
    fetchCurrentUser()
      .then((user) => setState({ status: "ready", user }))
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 401) {
          setState({ status: "ready", user: null });
        } else {
          setState({
            status: "error",
            message: error instanceof Error ? error.message : String(error)
          });
        }
      });
  }, []);

  useEffect(load, [load]);
  useEffect(() => onUnauthorized(() => setState({ status: "ready", user: null })), []);

  const signIn = useCallback(async (username: string, password: string) => {
    const user = await apiLogin(username, password);
    setState({ status: "ready", user });
    return user;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setState({ status: "ready", user: null });
    }
  }, []);

  const value = useMemo(
    () => ({ state, signIn, signOut, retry: load }),
    [state, signIn, signOut, load]
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
