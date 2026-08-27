import type { DraftView } from "@/lib/types"

export type FeedbackBadge = {
  label: string
  tone: "green" | "blue" | "red" | "amber"
} | null

export const feedbackBadge = (draft: DraftView): FeedbackBadge => {
  if (draft.feedback_action === "approve" && draft.edited_body) {
    return { label: "Edited & Approved", tone: "blue" }
  }
  if (draft.feedback_action === "approve" || draft.approved_at) {
    return { label: "Approved", tone: "green" }
  }
  if (draft.feedback_action === "reject" || draft.rejected_at) {
    return { label: "Rejected", tone: "red" }
  }
  if (draft.feedback_action === "wrong") {
    return { label: "Marked as no reply needed", tone: "amber" }
  }
  return null
}

export const firstUrgency = (
  presentationUrgency: string | null | undefined,
  threadUrgency: string | null | undefined,
  draftUrgency: string | null | undefined,
  classificationUrgency: string | null | undefined,
): string | null => {
  if (presentationUrgency) return presentationUrgency
  if (threadUrgency) return threadUrgency
  if (draftUrgency) return draftUrgency
  if (classificationUrgency) return classificationUrgency
  return null
}

export const isActivationKey = (key: string): boolean => key === "Enter" || key === " "

export const teachingNoteFor = (
  threadNote: string | null | undefined,
  draftNote: string | null | undefined,
): string | null => threadNote ?? draftNote ?? null

export const isFeedbackDone = (draft: DraftView | null): boolean => {
  if (!draft) return false
  return Boolean(draft.approved_at || draft.rejected_at || draft.feedback_action)
}
