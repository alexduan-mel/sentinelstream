// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { installDomStubs } from "../test/dom";
import { LoginPage, lockoutMessage } from "./LoginPage";

beforeAll(installDomStubs);

type Reply = { status: number; body?: unknown; headers?: Record<string, string> };

function mockApi(loginReplies: Reply[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/auth/me")
      return new Response(JSON.stringify({ detail: "Login required" }), { status: 401 });
    const reply = loginReplies.shift() ?? { status: 500 };
    return new Response(JSON.stringify(reply.body ?? {}), {
      status: reply.status,
      headers: reply.headers
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderLogin() {
  render(
    <MemoryRouter
      initialEntries={["/login"]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<p>alerts page</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

async function submit(username: string, password: string) {
  fireEvent.change(await screen.findByLabelText("Username"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => vi.useRealTimers());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("lockoutMessage", () => {
  it("rounds up to whole minutes", () => {
    expect(lockoutMessage(300)).toBe("Too many attempts. Try again in 5 minutes");
    expect(lockoutMessage(61)).toBe("Too many attempts. Try again in 2 minutes");
    expect(lockoutMessage(20)).toBe("Too many attempts. Try again in 1 minute");
  });
});

describe("LoginPage", () => {
  it("shows the inline error on a wrong password", async () => {
    mockApi([{ status: 401, body: { detail: "Incorrect username or password" } }]);
    renderLogin();

    await submit("admin", "wrong");

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Incorrect username or password"
    );
  });

  it("disables the button while locked out and re-enables it afterwards", async () => {
    mockApi([
      {
        status: 429,
        body: { detail: "Too many attempts", retry_after_seconds: 2 },
        headers: { "Retry-After": "2" }
      }
    ]);
    renderLogin();

    await submit("admin", "wrong");

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Too many attempts. Try again in 1 minute"
    );
    const button = screen.getByRole("button", { name: "Sign in" });
    expect(button).toHaveProperty("disabled", true);

    await act(() => new Promise((resolve) => setTimeout(resolve, 3100)));
    await waitFor(() => expect(button).toHaveProperty("disabled", false));
    expect(screen.queryByRole("alert")).toBeNull();
  }, 10_000);

  it("goes to the Alerts page after signing in", async () => {
    const fetchMock = mockApi([{ status: 200, body: { id: 1, username: "admin", role: "admin" } }]);
    renderLogin();

    await submit("admin", "right-password");

    expect(await screen.findByText("alerts page")).toBeTruthy();
    const [, init] = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/auth/login"
    ) as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      username: "admin",
      password: "right-password"
    });
  });
});
