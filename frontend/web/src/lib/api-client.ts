/**
 * Thin fetch wrapper for the FastAPI backend.
 * Access token is memory-only; refresh uses HttpOnly cookie + CSRF header.
 */

import {
  clearAuthSession,
  getAccessToken,
  setAuthSession,
} from "@/features/auth/auth-store";
import { messageForStatus } from "@/lib/error-messages";
import type {
  DashboardOverview,
  DraftView,
  ImportSkillResult,
  MailboxOverview,
  ReplyMemoryResponse,
  SkillCandidateResponse,
  SkillCreate,
  SkillFileMeta,
  SkillResponse,
  SkillUpdate,
  ThreadDetail,
  ThreadList,
  TokenResponse,
  ToneProfileResponse,
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

function detailFromErrorBody(body: unknown): string | null {
  if (body == null || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message.trim();
  }
  return null;
}

async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  retried = false,
): Promise<T> {
  const headers = new Headers(init.headers);
  const isFormData =
    typeof FormData !== "undefined" && init.body instanceof FormData;
  if (!headers.has("Content-Type") && init.body && !isFormData) {
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
    // Always map status → friendly copy (never surface bare codes like "429").
    throw new ApiError(messageForStatus(resp.status), resp.status, body);
  }

  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

/** Multipart upload helper — surfaces server `detail` text verbatim for import UX. */
async function apiFetchMultipart<T>(
  path: string,
  form: FormData,
  retried = false,
): Promise<T> {
  const headers = new Headers();
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const resp = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: form,
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
    if (ok) return apiFetchMultipart<T>(path, form, true);
  }

  if (!resp.ok) {
    let body: unknown;
    try {
      body = await resp.json();
    } catch {
      body = undefined;
    }
    const verbatim = detailFromErrorBody(body);
    throw new ApiError(
      verbatim ?? messageForStatus(resp.status),
      resp.status,
      body,
    );
  }

  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

async function apiFetchBytes(
  path: string,
  retried = false,
): Promise<{ blob: Blob; contentType: string }> {
  const headers = new Headers();
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const resp = await fetch(`${API_BASE}${path}`, {
    method: "GET",
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
    if (ok) return apiFetchBytes(path, true);
  }

  if (!resp.ok) {
    let body: unknown;
    try {
      body = await resp.json();
    } catch {
      body = undefined;
    }
    throw new ApiError(messageForStatus(resp.status), resp.status, body);
  }

  const contentType = resp.headers.get("Content-Type") ?? "application/octet-stream";
  const blob = await resp.blob();
  return { blob, contentType };
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

  drafts: {
    approve(id: string, body?: { edited_body?: string }) {
      return apiFetch<DraftView>(`/api/drafts/${id}/approve`, {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      });
    },
    reject(id: string, body: { feedback_note: string; reason_code: string }) {
      return apiFetch<DraftView>(`/api/drafts/${id}/reject`, {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
    markWrong(id: string, body: { feedback_note: string; reason_code?: string }) {
      return apiFetch<DraftView>(`/api/drafts/${id}/wrong`, {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
    regenerate(threadId: string, body: { instruction: string }) {
      return apiFetch<DraftView>(
        `/api/threads/${threadId}/regenerate-draft`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
    },
  },

  skills: {
    list() {
      return apiFetch<SkillResponse[]>("/api/skills");
    },
    create(body: SkillCreate) {
      return apiFetch<SkillResponse>("/api/skills", {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
    update(id: string, body: SkillUpdate) {
      return apiFetch<SkillResponse>(`/api/skills/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
    },
    delete(id: string) {
      return apiFetch<void>(`/api/skills/${id}`, {
        method: "DELETE",
      });
    },
    import(file: File, options?: { overwrite?: boolean; category?: string }) {
      const form = new FormData();
      form.append("file", file);
      if (options?.overwrite != null) {
        form.append("overwrite", String(options.overwrite));
      }
      if (options?.category) {
        form.append("category", options.category);
      }
      return apiFetchMultipart<ImportSkillResult>("/api/skills/import", form);
    },
    listFiles(id: string) {
      return apiFetch<SkillFileMeta[]>(`/api/skills/${id}/files`);
    },
    async getFile(id: string, relativePath: string) {
      const encoded = relativePath
        .split("/")
        .map((segment) => encodeURIComponent(segment))
        .join("/");
      return apiFetchBytes(`/api/skills/${id}/files/${encoded}`);
    },
  },

  replyMemory: {
    list(mailbox?: string) {
      const qs = new URLSearchParams();
      if (mailbox) qs.set("mailbox", mailbox);
      const query = qs.toString();
      return apiFetch<ReplyMemoryResponse[]>(
        `/api/reply-memory${query ? `?${query}` : ""}`,
      );
    },
    setExcluded(id: string, is_excluded: boolean) {
      return apiFetch<ReplyMemoryResponse>(`/api/reply-memory/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_excluded }),
      });
    },
  },

  toneProfiles: {
    list(mailbox?: string) {
      const qs = new URLSearchParams();
      if (mailbox) qs.set("mailbox", mailbox);
      const query = qs.toString();
      return apiFetch<ToneProfileResponse[]>(
        `/api/tone-profiles${query ? `?${query}` : ""}`,
      );
    },
  },

  skillCandidates: {
    list(mailbox?: string) {
      const qs = new URLSearchParams();
      if (mailbox) qs.set("mailbox", mailbox);
      const query = qs.toString();
      return apiFetch<SkillCandidateResponse[]>(
        `/api/skill-candidates${query ? `?${query}` : ""}`,
      );
    },
    accept(id: string) {
      return apiFetch<SkillResponse>(`/api/skill-candidates/${id}/accept`, {
        method: "POST",
      });
    },
    dismiss(id: string) {
      return apiFetch<SkillCandidateResponse>(
        `/api/skill-candidates/${id}/dismiss`,
        { method: "POST" },
      );
    },
  },
};

export { API_BASE };
