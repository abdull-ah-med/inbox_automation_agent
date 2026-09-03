import {
  StatusBadge,
  dispositionTone,
  urgencyTone,
  stateToneFromLabel,
} from "@/components/status-badge"

const KIND_TONE: Record<
  string,
  "neutral" | "blue" | "green" | "amber" | "red" | "purple" | "orange" | "teal"
> = {
  internal: "blue",
  automated: "neutral",
  automated_action: "amber",
  action_needed: "amber",
  needs_context: "amber",
  category: "purple",
  not_spam: "green",
}

const LABEL_TO_DISPOSITION: Record<string, string> = {
  "Reply ready": "reply_review",
  "Action needed": "action_no_draft",
  FYI: "fyi_briefing",
  "Waiting on them": "waiting_on_them",
  "Needs human": "needs_human",
  Processing: "processing",
  "Resolved by DraftAssistant": "resolved_draftassistant",
  "Resolved by Elise": "resolved_elise",
  Spam: "spam",
}

export const PresentationBadges = ({ badges }: { badges: { kind: string; label: string }[] }) => {
  if (badges.length === 0) return null
  return (
    <>
      {badges.map((badge) => {
        const tone =
          badge.kind === "urgency"
            ? urgencyTone(badge.label)
            : badge.kind === "disposition"
              ? dispositionTone(LABEL_TO_DISPOSITION[badge.label] ?? badge.label)
              : badge.kind === "state"
                ? stateToneFromLabel(badge.label)
                : (KIND_TONE[badge.kind] ?? "neutral")
        return <StatusBadge key={`${badge.kind}-${badge.label}`} label={badge.label} tone={tone} />
      })}
    </>
  )
}
