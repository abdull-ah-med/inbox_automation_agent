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
import {
  dispatchChatStreamBlock,
  type ChatStreamHandlers,
} from "@/lib/chat-stream";
import type {
  ChatAskRequest,
  ChatAskResponse,
  ChatSessionCreateResponse,
  ChatSessionResponse,
  DashboardOverview,
  DraftView,
  ImportSkillResult,
  MailboxOverview,
  RelatedThreadList,
  ReplyMemoryResponse,
  SearchResponse,
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

// Empty = same-origin (local Next rewrites / production nginx). Cross-origin
// only when NEXT_PUBLIC_API_BASE_URL is set explicitly.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";

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

export type SkillDuplicateCandidate = {
  id: string;
  name: string;
  similarity: number;
};

export class SkillDuplicateCandidatesError extends ApiError {
  candidates: SkillDuplicateCandidate[];

  constructor(
    candidates: SkillDuplicateCandidate[],
    status: number,
    body?: unknown,
  ) {
    super("Similar skills already exist", status, body);
    this.name = "SkillDuplicateCandidatesError";
    this.candidates = candidates;
  }
}

const parseDuplicateCandidates = (
  body: unknown,
): SkillDuplicateCandidate[] | null => {
  if (body == null || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (detail == null || typeof detail !== "object") return null;
  const coded = detail as {
    code?: unknown;
    candidates?: unknown;
  };
  if (coded.code !== "duplicate_candidates") return null;
  if (!Array.isArray(coded.candidates)) return null;
  const candidates: SkillDuplicateCandidate[] = [];
  for (const row of coded.candidates) {
    if (row == null || typeof row !== "object") continue;
    const item = row as {
      id?: unknown;
      name?: unknown;
      similarity?: unknown;
    };
    if (typeof item.id !== "string" || typeof item.name !== "string") continue;
    if (typeof item.similarity !== "number") continue;
    candidates.push({
      id: item.id,
      name: item.name,
      similarity: item.similarity,
    });
  }
  return candidates;
};

export type ImportSkillOptions = {
  overwrite?: boolean;
  overwriteSkillId?: string;
  nameOverride?: string;
  category?: string;
};

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
    !path.startsWith("/auth/logout") &&
    !path.startsWith("/auth/change-password")
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
    !path.startsWith("/auth/logout") &&
    !path.startsWith("/auth/change-password")
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
    const duplicates = parseDuplicateCandidates(body);
    if (duplicates != null) {
      throw new SkillDuplicateCandidatesError(duplicates, resp.status, body);
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

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      return star[1].trim();
    }
  }
  const quoted = /filename="([^"]+)"/i.exec(header);
  if (quoted?.[1]) return quoted[1];
  const plain = /filename=([^;]+)/i.exec(header);
  if (plain?.[1]) return plain[1].trim().replaceAll('"', "");
  return null;
}

async function apiFetchBytes(
  path: string,
  retried = false,
): Promise<{ blob: Blob; contentType: string; filename: string | null }> {
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
    !path.startsWith("/auth/logout") &&
    !path.startsWith("/auth/change-password")
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
  const filename = filenameFromDisposition(resp.headers.get("Content-Disposition"));
  return { blob, contentType, filename };
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

  async changePassword(currentPassword: string, newPassword: string) {
    const csrf = readCookie(CSRF_COOKIE);
    if (!csrf) {
      throw new ApiError("Missing CSRF token", 403);
    }
    await apiFetch<void>("/auth/change-password", {
      method: "POST",
      headers: { [CSRF_HEADER]: csrf },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
    clearAuthSession();
  },

  dashboard: {
    overview() {
      return apiFetch<DashboardOverview>("/api/dashboard/overview");
    },
  },

  reports: {
    downloadWeekly(params: { from?: string; to?: string; mailbox?: string } = {}) {
      const qs = new URLSearchParams();
      if (params.from) qs.set("from", params.from);
      if (params.to) qs.set("to", params.to);
      if (params.mailbox) qs.set("mailbox", params.mailbox);
      const query = qs.toString();
      return apiFetchBytes(`/api/reports/ops-weekly${query ? `?${query}` : ""}`);
    },
  },

  search: {
    threads(params: { q: string; mailbox?: string; limit?: number; mode?: "keyword" | "hybrid" }) {
      const qs = new URLSearchParams();
      qs.set("q", params.q);
      if (params.mailbox) qs.set("mailbox", params.mailbox);
      if (params.limit) qs.set("limit", String(params.limit));
      if (params.mode) qs.set("mode", params.mode);
      return apiFetch<SearchResponse>(`/api/search?${qs.toString()}`);
    },
  },

  chat: {
    createSession(body: { mailbox?: string | null } = {}) {
      return apiFetch<ChatSessionCreateResponse>("/api/chat/session", {
        method: "POST",
        body: JSON.stringify(body),
      })
    },
    getSession(sessionId: string) {
      return apiFetch<ChatSessionResponse>(`/api/chat/session/${sessionId}`)
    },
    ask(body: ChatAskRequest) {
      return apiFetch<ChatAskResponse>("/api/chat/ask", {
        method: "POST",
        body: JSON.stringify(body),
      })
    },
    async askStream(
      body: ChatAskRequest,
      handlers: ChatStreamHandlers,
      signal?: AbortSignal,
    ) {
      const headers = new Headers()
      headers.set("Content-Type", "application/json")
      headers.set("Accept", "text/event-stream")
      const token = getAccessToken()
      if (token) {
        headers.set("Authorization", `Bearer ${token}`)
      }
      const post = (requestHeaders: Headers) =>
        fetch(`${API_BASE}/api/chat/ask/stream`, {
          method: "POST",
          headers: requestHeaders,
          credentials: "include",
          body: JSON.stringify(body),
          signal,
        })
      const jitter = (attempt: number) =>
        80 * 2 ** attempt + Math.floor(Math.random() * 40)
      let sawDelta = false
      let lastError: unknown
      for (let attempt = 0; attempt < 3; attempt += 1) {
        if (attempt > 0 && sawDelta) break
        let streamFinished = false
        try {
          let resp = await post(headers)
          if (resp.status === 401) {
            const ok = await refreshAccessToken()
            if (ok) {
              const retryHeaders = new Headers(headers)
              const nextToken = getAccessToken()
              if (nextToken) {
                retryHeaders.set("Authorization", `Bearer ${nextToken}`)
              }
              resp = await fetch(`${API_BASE}/api/chat/ask/stream`, {
                method: "POST",
                headers: retryHeaders,
                credentials: "include",
                body: JSON.stringify(body),
                signal,
              })
            }
          }
          if (!resp.ok) {
            let errorBody: unknown
            try {
              errorBody = await resp.json()
            } catch {
              errorBody = undefined
            }
            throw new ApiError(messageForStatus(resp.status), resp.status, errorBody)
          }
          if (!resp.body) {
            throw new ApiError(messageForStatus(502), 502)
          }
          const reader = resp.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ""
          let sawError = false
          const wrapped: ChatStreamHandlers = {
            ...handlers,
            onDelta: (text) => {
              if (text) sawDelta = true
              handlers.onDelta?.(text)
            },
            onError: (message) => {
              sawError = true
              handlers.onError?.(message)
            },
          }
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            const parts = buffer.split("\n\n")
            buffer = parts.pop() ?? ""
            for (const part of parts) {
              const status = dispatchChatStreamBlock(part, wrapped)
              if (status === "error") {
                throw new ApiError("Claude chat failed", 502)
              }
            }
          }
          if (buffer.trim()) {
            const status = dispatchChatStreamBlock(buffer, wrapped)
            if (status === "error") {
              throw new ApiError("Claude chat failed", 502)
            }
          }
          streamFinished = true
          if (sawError) {
            throw new ApiError("Claude chat failed", 502)
          }
          if (!sawDelta) {
            throw new ApiError(messageForStatus(502), 502)
          }
          return
        } catch (error) {
          lastError = error
          const aborted =
            signal?.aborted ||
            (error instanceof DOMException && error.name === "AbortError") ||
            (error instanceof Error && error.name === "AbortError")
          if (aborted || sawDelta || streamFinished || attempt === 2) {
            throw error
          }
          await new Promise((resolve) => {
            window.setTimeout(resolve, jitter(attempt))
          })
        }
      }
      throw lastError instanceof Error
        ? lastError
        : new ApiError(messageForStatus(502), 502)
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
    related(id: string, purpose: "siblings" | "associated") {
      return apiFetch<RelatedThreadList>(
        `/api/threads/${id}/related?purpose=${encodeURIComponent(purpose)}`,
      );
    },
    applyTreatment(
      id: string,
      body: {
        treatment: "no_reply" | "urgency";
        thread_ids: string[];
        reason: string;
        urgency?: "CRITICAL" | "HIGH" | "NORMAL" | "LOW";
      },
    ) {
      return apiFetch<{ applied_thread_ids: string[] }>(
        `/api/threads/${id}/apply-treatment`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
    },
    reviewRelated(
      id: string,
      relatedId: string,
      body: { status: "confirmed" | "dismissed" },
    ) {
      return apiFetch<{ status: "confirmed" | "dismissed" }>(
        `/api/threads/${id}/related/${relatedId}/review`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
    },
    markNotSpam(id: string) {
      return apiFetch<{
        thread_id: string;
        state: string;
        is_spam: boolean;
        sender_address: string;
        outlook_unchanged: boolean;
      }>(`/api/threads/${id}/not-spam`, {
        method: "POST",
      });
    },
    resolve(id: string, body?: { note?: string }) {
      return apiFetch<{ state: string }>(`/api/threads/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      });
    },
    resolutionFeedback(
      id: string,
      body: { action: "reopen" | "wrong_reason"; note?: string },
    ) {
      return apiFetch<{ state: string; action: string }>(
        `/api/threads/${id}/resolution-feedback`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
    },
  },

  drafts: {
    approve(
      id: string,
      body?: {
        edited_body?: string
        approval_note?: string
        approval_scope?: "once" | "similar"
      },
    ) {
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
    editUrgency(
      id: string,
      body: {
        new_urgency: "CRITICAL" | "HIGH" | "NORMAL" | "LOW"
        reason: string
      },
    ) {
      return apiFetch<{
        urgency: string
        urgency_reason: string
        updated_at: string
        draft_id: string
        thread_id: string
      }>(`/api/drafts/${id}/urgency`, {
        method: "POST",
        body: JSON.stringify(body),
      });
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
    import(file: File, options?: ImportSkillOptions) {
      const form = new FormData();
      form.append("file", file);
      if (options?.overwrite != null) {
        form.append("overwrite", String(options.overwrite));
      }
      if (options?.overwriteSkillId) {
        form.append("overwrite_skill_id", options.overwriteSkillId);
      }
      if (options?.nameOverride) {
        form.append("name_override", options.nameOverride);
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
