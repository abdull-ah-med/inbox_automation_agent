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
