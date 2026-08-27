import { apiFetch } from "@/lib/api/client"
import { askStreamWithRetry } from "@/lib/api/chat-stream-ask"
import type { ChatStreamHandlers } from "@/lib/chat-stream"
import type { ChatAskRequest, ChatSessionCreateResponse, ChatSessionResponse } from "@/lib/types"

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
  async askStream(body: ChatAskRequest, handlers: ChatStreamHandlers, signal?: AbortSignal) {
    await askStreamWithRetry(body, handlers, signal)
  },
}
