/**
 * Thin fetch wrapper for the FastAPI backend.
 * Access token is memory-only; refresh uses HttpOnly cookie + CSRF header.
 */

import {
  clearAuthSession,
  getAccessToken,
  setAuthSession,
} from "@/features/auth/auth-store";
import type {
  DashboardOverview,
  MailboxOverview,
  ThreadDetail,
  ThreadList,
  TokenResponse,
  UserMe,
} from "@/lib/types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ??
  "http://localhost:8000";

const CSRF_COOKIE = "itr_csrf";
const CSRF_HEADER = "X-CSRF-Token";

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`));
  if (!match) return null;
  return decodeURIComponent(match.slice(name.length + 1));
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let refreshPromise: Promise<boolean> | null = null;

async function clearServerSession(): Promise<void> {
  const csrf = readCookie(CSRF_COOKIE);
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: csrf ? { [CSRF_HEADER]: csrf } : {},
    });
  } catch {
    // Best-effort cookie clear; ignore network errors.
  } finally {
    clearAuthSession();
  }
}

async function refreshAccessToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      const csrf = readCookie(CSRF_COOKIE);
      if (!csrf) {
        await clearServerSession();
        return false;
      }
      const resp = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: {
          [CSRF_HEADER]: csrf,
        },
      });
      if (!resp.ok) {
        // Drop HttpOnly refresh cookie so middleware stops treating us as signed in.
        // Includes 429: better to force re-login than spin a redirect loop.
        await clearServerSession();
        return false;
      }
      const data = (await resp.json()) as TokenResponse;
      setAuthSession({
        accessToken: data.access_token,
        expiresIn: data.expires_in,
        user: data.user,
      });
      return true;
    } catch {
      await clearServerSession();
      return false;
    }
  })().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  retried = false,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (
    resp.status === 401 &&
    !retried &&
    !path.startsWith("/auth/login") &&
    !path.startsWith("/auth/refresh") &&
    !path.startsWith("/auth/logout")
  ) {
    const ok = await refreshAccessToken();
    if (ok) return apiFetch<T>(path, init, true);
  }

  if (!resp.ok) {
    let body: unknown;
    try {
      body = await resp.json();
    } catch {
      body = undefined;
    }
    const detail =
      typeof body === "object" &&
      body &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
        ? (body as { detail: string }).detail
        : `Request failed (${resp.status})`;
    throw new ApiError(detail, resp.status, body);
  }

  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export const api = {
  login(email: string, password: string) {
    return apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then((data) => {
      setAuthSession({
        accessToken: data.access_token,
        expiresIn: data.expires_in,
        user: data.user,
      });
      return data;
    });
  },

  async logout() {
    await clearServerSession();
  },

  refresh: refreshAccessToken,

  me() {
    return apiFetch<UserMe>("/auth/me");
  },

  dashboard: {
    overview() {
      return apiFetch<DashboardOverview>("/api/dashboard/overview");
    },
  },

  mailboxes: {
    list() {
      return apiFetch<MailboxOverview[]>("/api/mailboxes");
    },
    threads(
      mailbox: string,
      params: {
        state?: string;
        urgency?: string;
        stale_only?: boolean;
        include_filtered?: boolean;
        cursor?: string;
        limit?: number;
      } = {},
    ) {
      const qs = new URLSearchParams();
      if (params.state) qs.set("state", params.state);
      if (params.urgency) qs.set("urgency", params.urgency);
      if (params.stale_only) qs.set("stale_only", "true");
      if (params.include_filtered) qs.set("include_filtered", "true");
      if (params.cursor) qs.set("cursor", params.cursor);
      if (params.limit) qs.set("limit", String(params.limit));
      const query = qs.toString();
      return apiFetch<ThreadList>(
        `/api/mailboxes/${encodeURIComponent(mailbox)}/threads${query ? `?${query}` : ""}`,
      );
    },
  },

  threads: {
    detail(id: string) {
      return apiFetch<ThreadDetail>(`/api/threads/${id}`);
    },
  },
};

export { API_BASE };
