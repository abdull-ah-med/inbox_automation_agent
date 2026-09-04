/**
 * Thin fetch wrapper for the FastAPI backend.
 * Access token is memory-only; refresh uses HttpOnly cookie + CSRF header.
 */

import { clearAuthSession, getAccessToken, setAuthSession } from "@/features/auth/auth-store"
import { messageForStatus } from "@/lib/error-messages"
import type { ImportSkillOptions, SkillDuplicateCandidate, TokenResponse } from "@/lib/types"

export type { ImportSkillOptions, SkillDuplicateCandidate }

// Empty = same-origin (local Next rewrites / production nginx). Cross-origin
// only when NEXT_PUBLIC_API_BASE_URL is set explicitly.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? ""

export const CSRF_COOKIE = "itr_csrf"
export const CSRF_HEADER = "X-CSRF-Token"

const AUTH_EXEMPT_PATHS = [
  "/auth/login",
  "/auth/refresh",
  "/auth/logout",
  "/auth/change-password",
] as const

const isAuthExemptPath = (path: string) =>
  AUTH_EXEMPT_PATHS.some((prefix) => path.startsWith(prefix))

export const buildAuthHeaders = (init?: HeadersInit, contentType?: string) => {
  const headers = new Headers(init)
  if (contentType && !headers.has("Content-Type")) {
    headers.set("Content-Type", contentType)
  }
  const token = getAccessToken()
  if (token) {
    headers.set("Authorization", `Bearer ${token}`)
  }
  return headers
}

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null
  const match = document.cookie.split("; ").find((row) => row.startsWith(`${name}=`))
  if (!match) return null
  return decodeURIComponent(match.slice(name.length + 1))
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: unknown,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

let refreshPromise: Promise<boolean> | null = null

export async function clearServerSession(): Promise<void> {
  const csrf = readCookie(CSRF_COOKIE)
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: csrf ? { [CSRF_HEADER]: csrf } : {},
    })
  } catch {
    // Best-effort cookie clear; ignore network errors.
  } finally {
    clearAuthSession()
  }
}

export async function refreshAccessToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshPromise = (async () => {
    try {
      const csrf = readCookie(CSRF_COOKIE)
      if (!csrf) {
        await clearServerSession()
        return false
      }
      const resp = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: {
          [CSRF_HEADER]: csrf,
        },
      })
      if (!resp.ok) {
        // Drop HttpOnly refresh cookie so middleware stops treating us as signed in.
        // Includes 429: better to force re-login than spin a redirect loop.
        await clearServerSession()
        return false
      }
      const data = (await resp.json()) as TokenResponse
      setAuthSession({
        accessToken: data.access_token,
        expiresIn: data.expires_in,
        user: data.user,
      })
      return true
    } catch {
      await clearServerSession()
      return false
    }
  })().finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

function detailFromErrorBody(body: unknown): string | null {
  if (body == null || typeof body !== "object") return null
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === "string" && detail.trim()) return detail.trim()
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message
    if (typeof message === "string" && message.trim()) return message.trim()
  }
  return null
}

export class SkillDuplicateCandidatesError extends ApiError {
  candidates: SkillDuplicateCandidate[]

  constructor(candidates: SkillDuplicateCandidate[], status: number, body?: unknown) {
    super("Similar skills already exist", status, body)
    this.name = "SkillDuplicateCandidatesError"
    this.candidates = candidates
  }
}

const parseDuplicateCandidates = (body: unknown): SkillDuplicateCandidate[] | null => {
  if (body == null || typeof body !== "object") return null
  const detail = (body as { detail?: unknown }).detail
  if (detail == null || typeof detail !== "object") return null
  const coded = detail as {
    code?: unknown
    candidates?: unknown
  }
  if (coded.code !== "duplicate_candidates") return null
  if (!Array.isArray(coded.candidates)) return null
  const candidates: SkillDuplicateCandidate[] = []
  for (const row of coded.candidates) {
    if (row == null || typeof row !== "object") continue
    const item = row as {
      id?: unknown
      name?: unknown
      similarity?: unknown
    }
    if (typeof item.id !== "string" || typeof item.name !== "string") continue
    if (typeof item.similarity !== "number") continue
    candidates.push({
      id: item.id,
      name: item.name,
      similarity: item.similarity,
    })
  }
  return candidates
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  retried = false,
): Promise<T> {
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData
  const headers = buildAuthHeaders(
    init.headers,
    !isFormData && init.body ? "application/json" : undefined,
  )

  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  })

  if (resp.status === 401 && !retried && !isAuthExemptPath(path)) {
    const ok = await refreshAccessToken()
    if (ok) return apiFetch<T>(path, init, true)
  }

  if (!resp.ok) {
    let body: unknown
    try {
      body = await resp.json()
    } catch {
      body = undefined
    }
    // Always map status → friendly copy (never surface bare codes like "429").
    throw new ApiError(messageForStatus(resp.status), resp.status, body)
  }

  if (resp.status === 204) {
    return undefined as T
  }
  const text = await resp.text()
  if (!text) {
    return undefined as T
  }
  return JSON.parse(text) as T
}

/** Multipart upload helper — surfaces server `detail` text verbatim for import UX. */
export async function apiFetchMultipart<T>(
  path: string,
  form: FormData,
  retried = false,
): Promise<T> {
  const headers = buildAuthHeaders()

  const resp = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: form,
    credentials: "include",
  })

  if (resp.status === 401 && !retried && !isAuthExemptPath(path)) {
    const ok = await refreshAccessToken()
    if (ok) return apiFetchMultipart<T>(path, form, true)
  }

  if (!resp.ok) {
    let body: unknown
    try {
      body = await resp.json()
    } catch {
      body = undefined
    }
    const duplicates = parseDuplicateCandidates(body)
    if (duplicates != null) {
      throw new SkillDuplicateCandidatesError(duplicates, resp.status, body)
    }
    const verbatim = detailFromErrorBody(body)
    throw new ApiError(verbatim ?? messageForStatus(resp.status), resp.status, body)
  }

  if (resp.status === 204) {
    return undefined as T
  }
  return (await resp.json()) as T
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      return star[1].trim()
    }
  }
  const quoted = /filename="([^"]+)"/i.exec(header)
  if (quoted?.[1]) return quoted[1]
  const plain = /filename=([^;]+)/i.exec(header)
  if (plain?.[1]) return plain[1].trim().replaceAll('"', "")
  return null
}

export async function apiFetchBytes(
  path: string,
  retried = false,
): Promise<{ blob: Blob; contentType: string; filename: string | null }> {
  const headers = buildAuthHeaders()

  const resp = await fetch(`${API_BASE}${path}`, {
    method: "GET",
    headers,
    credentials: "include",
  })

  if (resp.status === 401 && !retried && !isAuthExemptPath(path)) {
    const ok = await refreshAccessToken()
    if (ok) return apiFetchBytes(path, true)
  }

  if (!resp.ok) {
    let body: unknown
    try {
      body = await resp.json()
    } catch {
      body = undefined
    }
    throw new ApiError(messageForStatus(resp.status), resp.status, body)
  }

  const contentType = resp.headers.get("Content-Type") ?? "application/octet-stream"
  const blob = await resp.blob()
  const filename = filenameFromDisposition(resp.headers.get("Content-Disposition"))
  return { blob, contentType, filename }
}
