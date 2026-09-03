import { apiFetch } from "@/lib/api/client"
import type { MarkNotSpamResponse, RelatedThreadList, ThreadDetail, DraftView } from "@/lib/types"

export type MessageHtmlBody = {
  content_type: "html" | "text"
  html: string
}

export const threadsApi = {
  detail(id: string) {
    return apiFetch<ThreadDetail>(`/api/threads/${id}`)
  },
  header(id: string) {
    return apiFetch<{ subject: string; mailbox: string }>(`/api/threads/${id}/header`)
  },
  getMessageHtml(threadId: string, messageId: string, signal?: AbortSignal) {
    return apiFetch<MessageHtmlBody>(`/api/threads/${threadId}/messages/${messageId}/html`, {
      signal,
    })
  },
  related(id: string, purpose: "siblings" | "associated") {
    return apiFetch<RelatedThreadList>(
      `/api/threads/${id}/related?purpose=${encodeURIComponent(purpose)}`,
    )
  },
  applyTreatment(
    id: string,
    body: {
      treatment: "no_reply" | "urgency"
      thread_ids: string[]
      reason: string
      urgency?: "CRITICAL" | "HIGH" | "NORMAL" | "LOW"
    },
  ) {
    return apiFetch<{ applied_thread_ids: string[] }>(`/api/threads/${id}/apply-treatment`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  },
  reviewRelated(
    id: string,
    relatedId: string,
    body: { status: "confirmed" | "dismissed" },
    signal?: AbortSignal,
  ) {
    return apiFetch<{ status: "confirmed" | "dismissed" }>(
      `/api/threads/${id}/related/${relatedId}/review`,
      {
        method: "POST",
        body: JSON.stringify(body),
        signal,
      },
    )
  },
  markNotSpam(id: string) {
    return apiFetch<MarkNotSpamResponse>(`/api/threads/${id}/not-spam`, {
      method: "POST",
    })
  },
  resolve(id: string, body: { actions_taken: string; involved?: string | null }) {
    return apiFetch<{ state: string }>(`/api/threads/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  },
  generateDraft(id: string) {
    return apiFetch<DraftView>(`/api/threads/${id}/generate-draft`, {
      method: "POST",
    })
  },
  resolutionFeedback(id: string, body: { action: "reopen" | "wrong_reason"; note?: string }) {
    return apiFetch<{ state: string; action: string }>(`/api/threads/${id}/resolution-feedback`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  },
  urgencyFeedback(id: string, body: { action: "wrong_escalation"; note?: string }) {
    return apiFetch<{ state: string; action: string; urgency?: string | null }>(
      `/api/threads/${id}/urgency-feedback`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    )
  },
}
