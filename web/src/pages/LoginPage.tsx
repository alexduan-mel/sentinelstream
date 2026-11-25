import { useEffect, useState } from "react";
import { Button, Form, Input, Typography } from "antd";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

type Credentials = { username: string; password: string };

export const INCORRECT_CREDENTIALS = "Incorrect username or password";

export function lockoutMessage(remainingSeconds: number): string {
  const minutes = Math.max(1, Math.ceil(remainingSeconds / 60));
  return `Too many attempts. Try again in ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export function LoginPage() {
  const { state, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const now = useNow(lockedUntil !== null);
  const locked = lockedUntil !== null && now < lockedUntil;

  useEffect(() => {
    if (lockedUntil !== null && now >= lockedUntil) {
      setLockedUntil(null);
      setError(null);
    }
  }, [lockedUntil, now]);

  if (state.status === "ready" && state.user !== null) {
    return <Navigate to={from} replace />;
  }

  const onFinish = async ({ username, password }: Credentials) => {
    setSubmitting(true);
    setError(null);
    try {
      await signIn(username, password);
      navigate(from, { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 429) {
        setLockedUntil(Date.now() + (caught.retryAfterSeconds ?? 60) * 1000);
      } else if (caught instanceof ApiError && caught.status === 401) {
        setError(INCORRECT_CREDENTIALS);
      } else {
        setError(`Sign-in failed (${caught instanceof Error ? caught.message : String(caught)})`);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const message =
    locked && lockedUntil !== null ? lockoutMessage((lockedUntil - now) / 1000) : error;

  return (
    <div className="flex min-h-screen items-center justify-center p-24">
      <div className="w-[360px]">
        <Typography.Title level={1} className="!mb-24 !text-page-title">
          SentinelStream
        </Typography.Title>
        <Form<Credentials>
          layout="vertical"
          requiredMark={false}
          onFinish={onFinish}
          disabled={submitting}
        >
          <Form.Item
            label="Username"
            name="username"
            rules={[{ required: true, whitespace: true, message: "Enter your username" }]}
          >
            <Input autoFocus autoComplete="username" />
          </Form.Item>
          <Form.Item
            label="Password"
            name="password"
            rules={[{ required: true, message: "Enter your password" }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          {message && (
            <Typography.Paragraph type="danger" className="!mb-16" role="alert">
              {message}
            </Typography.Paragraph>
          )}
          <Button type="primary" htmlType="submit" block loading={submitting} disabled={locked}>
            Sign in
          </Button>
        </Form>
      </div>
    </div>
  );
}
