import { getAccessToken } from "@/features/auth/auth-store"
import { messageForStatus } from "@/lib/error-messages"
import {
  dispatchChatStreamBlock,
  type ChatStreamHandlers,
} from "@/lib/chat-stream"
import {
  API_BASE,
  ApiError,
  apiFetch,
  buildAuthHeaders,
  refreshAccessToken,
} from "@/lib/api/client"
import type {
  ChatAskRequest,
  ChatSessionCreateResponse,
  ChatSessionResponse,
} from "@/lib/types"

export const chatApi = {
  createSession(body: { mailbox?: string | null } = {}) {
    return apiFetch<ChatSessionCreateResponse>("/api/chat/session", {
      method: "POST",
      body: JSON.stringify(body),
    })
  },
  getSession(sessionId: string) {
    return apiFetch<ChatSessionResponse>(`/api/chat/session/${sessionId}`)
  },
  async askStream(
    body: ChatAskRequest,
    handlers: ChatStreamHandlers,
    signal?: AbortSignal,
  ) {
    const headers = buildAuthHeaders(undefined, "application/json")
    headers.set("Accept", "text/event-stream")
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
        const onAbort = () => {
          void reader.cancel().catch(() => {})
        }
        signal?.addEventListener("abort", onAbort)
        try {
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
        } finally {
          signal?.removeEventListener("abort", onAbort)
          await reader.cancel().catch(() => {})
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
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, jitter(attempt))
          if (!signal) return
          const wakeEarly = () => {
            window.clearTimeout(timer)
            resolve()
          }
          signal.addEventListener("abort", wakeEarly, { once: true })
        })
      }
    }
    throw lastError instanceof Error
      ? lastError
      : new ApiError(messageForStatus(502), 502)
  },
}
