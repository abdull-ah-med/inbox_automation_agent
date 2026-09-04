import { apiFetch } from "@/lib/api/client"
import type {
  FeedbackAtom,
  PromotionProposal,
  TeachingNote,
  TeachingNoteCreateScope,
  UrgencyRule,
} from "@/lib/types"

export type TeachingNoteCreateBody = {
  mailbox: string
  title: string
  body: string
  applies_when?: string | null
  scope: TeachingNoteCreateScope
  sender_address?: string | null
  sender_domain?: string | null
  routing_category?: string | null
  person_bound?: boolean
}

export const teachingNotesApi = {
  list(params: { mailbox: string; scope?: string; status?: string }) {
    const qs = new URLSearchParams()
    qs.set("mailbox", params.mailbox)
    if (params.scope) qs.set("scope", params.scope)
    if (params.status) qs.set("status", params.status)
    return apiFetch<TeachingNote[]>(`/api/teaching-notes?${qs.toString()}`)
  },
  create(body: TeachingNoteCreateBody) {
    return apiFetch<TeachingNote>("/api/teaching-notes", {
      method: "POST",
      body: JSON.stringify(body),
    })
  },
  patch(
    id: string,
    body: {
      title?: string
      body?: string
      applies_when?: string | null
      scope?: string
      status?: string
    },
  ) {
    return apiFetch<TeachingNote>(`/api/teaching-notes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    })
  },
  remove(id: string) {
    return apiFetch<void>(`/api/teaching-notes/${id}`, { method: "DELETE" })
  },
}

export const feedbackAtomsApi = {
  list(params: { mailbox: string; role?: string; scope?: string }) {
    const qs = new URLSearchParams()
    qs.set("mailbox", params.mailbox)
    if (params.role) qs.set("role", params.role)
    if (params.scope) qs.set("scope", params.scope)
    return apiFetch<FeedbackAtom[]>(`/api/feedback-atoms?${qs.toString()}`)
  },
  exclude(id: string) {
    return apiFetch<FeedbackAtom>(`/api/feedback-atoms/${id}/exclude`, { method: "POST" })
  },
}

export const promotionProposalsApi = {
  list(params: { mailbox?: string; kind?: string; status?: string } = {}) {
    const qs = new URLSearchParams()
    if (params.mailbox) qs.set("mailbox", params.mailbox)
    if (params.kind) qs.set("kind", params.kind)
    if (params.status) qs.set("status", params.status)
    const query = qs.toString()
    return apiFetch<PromotionProposal[]>(`/api/promotion-proposals${query ? `?${query}` : ""}`)
  },
  accept(id: string, opts?: { confirm_high_change_rate?: boolean }) {
    const qs = opts?.confirm_high_change_rate ? "?confirm_high_change_rate=true" : ""
    return apiFetch<PromotionProposal>(`/api/promotion-proposals/${id}/accept${qs}`, {
      method: "POST",
    })
  },
  dismiss(id: string) {
    return apiFetch<PromotionProposal>(`/api/promotion-proposals/${id}/dismiss`, { method: "POST" })
  },
  revert(id: string) {
    return apiFetch<PromotionProposal>(`/api/promotion-proposals/${id}/revert`, { method: "POST" })
  },
}

export const urgencyRulesApi = {
  list(params: { mailbox: string; status?: string }) {
    const qs = new URLSearchParams()
    qs.set("mailbox", params.mailbox)
    if (params.status) qs.set("status", params.status)
    return apiFetch<UrgencyRule[]>(`/api/urgency-rules?${qs.toString()}`)
  },
  pause(id: string) {
    return apiFetch<UrgencyRule>(`/api/urgency-rules/${id}/pause`, { method: "POST" })
  },
  resume(id: string) {
    return apiFetch<UrgencyRule>(`/api/urgency-rules/${id}/resume`, { method: "POST" })
  },
  archive(id: string) {
    return apiFetch<UrgencyRule>(`/api/urgency-rules/${id}/archive`, { method: "POST" })
  },
}
