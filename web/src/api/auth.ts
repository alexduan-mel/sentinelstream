import { apiFetch } from "./client";

export type User = {
  id: number;
  username: string;
  role: "admin" | "user";
};

export function login(username: string, password: string): Promise<User> {
  return apiFetch<User>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
}

export async function logout(): Promise<void> {
  await apiFetch<null>("/api/auth/logout", { method: "POST" });
}

export function fetchCurrentUser(): Promise<User> {
  return apiFetch<User>("/api/auth/me");
}
