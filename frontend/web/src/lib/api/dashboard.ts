import { apiFetch, apiFetchBytes } from "@/lib/api/client"
import type {
  DashboardOverview,
  DraftView,
  ReplyMemoryResponse,
  SearchResponse,
  ToneProfileResponse,
} from "@/lib/types"

export const dashboardApi = {
  overview() {
    return apiFetch<DashboardOverview>("/api/dashboard/overview")
  },
}

export const reportsApi = {
  downloadWeekly(params: { from?: string; to?: string; mailbox?: string } = {}) {
    const qs = new URLSearchParams()
    if (params.from) qs.set("from", params.from)
    if (params.to) qs.set("to", params.to)
    if (params.mailbox) qs.set("mailbox", params.mailbox)
    const query = qs.toString()
    return apiFetchBytes(`/api/reports/ops-weekly${query ? `?${query}` : ""}`)
  },
}

export const searchApi = {
  threads(params: { q: string; mailbox?: string; limit?: number; mode?: "keyword" | "hybrid" }) {
    const qs = new URLSearchParams()
    qs.set("q", params.q)
    if (params.mailbox) qs.set("mailbox", params.mailbox)
    if (params.limit) qs.set("limit", String(params.limit))
    if (params.mode) qs.set("mode", params.mode)
    return apiFetch<SearchResponse>(`/api/search?${qs.toString()}`)
  },
}

export const draftsApi = {
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
    })
  },
  reject(id: string, body: { feedback_note: string; reason_code: string }) {
    return apiFetch<DraftView>(`/api/drafts/${id}/reject`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  },
  markWrong(id: string, body: { feedback_note: string; reason_code?: string }) {
    return apiFetch<DraftView>(`/api/drafts/${id}/wrong`, {
      method: "POST",
      body: JSON.stringify(body),
    })
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
    })
  },
}

export const replyMemoryApi = {
  list(mailbox?: string) {
    const qs = new URLSearchParams()
    if (mailbox) qs.set("mailbox", mailbox)
    const query = qs.toString()
    return apiFetch<ReplyMemoryResponse[]>(`/api/reply-memory${query ? `?${query}` : ""}`)
  },
  setExcluded(id: string, is_excluded: boolean) {
    return apiFetch<ReplyMemoryResponse>(`/api/reply-memory/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_excluded }),
    })
  },
}

export const toneProfilesApi = {
  list(mailbox?: string) {
    const qs = new URLSearchParams()
    if (mailbox) qs.set("mailbox", mailbox)
    const query = qs.toString()
    return apiFetch<ToneProfileResponse[]>(`/api/tone-profiles${query ? `?${query}` : ""}`)
  },
}
