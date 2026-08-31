export type RoutingCategory =
  | "billing"
  | "scheduling"
  | "escalation"
  | "vendor"
  | "internal"
  | "general"

export type RejectReasonCode =
  | "tone"
  | "factual"
  | "wrong_action"
  | "incomplete"
  | "policy"
  | "recipients"
  | "other"

export const ROUTING_CATEGORIES: RoutingCategory[] = [
  "billing",
  "scheduling",
  "escalation",
  "vendor",
  "internal",
  "general",
]

export const REJECT_REASON_CODES: RejectReasonCode[] = [
  "tone",
  "factual",
  "wrong_action",
  "incomplete",
  "policy",
  "recipients",
  "other",
]

export const REJECT_REASON_LABELS: Record<RejectReasonCode, string> = {
  tone: "Tone off",
  factual: "Factual error",
  wrong_action: "Wrong action / no reply needed",
  incomplete: "Incomplete",
  policy: "Policy conflict",
  recipients: "Wrong recipients",
  other: "Other",
}
