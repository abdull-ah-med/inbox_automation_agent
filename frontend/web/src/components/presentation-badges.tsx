import { StatusBadge, urgencyTone, stateToneFromLabel } from "@/components/status-badge"

const KIND_TONE: Record<string, "neutral" | "blue" | "green" | "amber" | "red" | "purple" | "orange"> = {
  internal: "blue",
  automated: "neutral",
  automated_action: "amber",
  action_needed: "amber",
  needs_context: "amber",
  category: "purple",
  not_spam: "green",
}

export const PresentationBadges = ({ badges }: { badges: { kind: string; label: string }[] }) => {
  if (badges.length === 0) return null
  return (
    <>
      {badges.map((badge) => {
        const tone =
          badge.kind === "urgency"
            ? urgencyTone(badge.label)
            : badge.kind === "state"
              ? stateToneFromLabel(badge.label)
              : (KIND_TONE[badge.kind] ?? "neutral")
        return <StatusBadge key={`${badge.kind}-${badge.label}`} label={badge.label} tone={tone} />
      })}
    </>
  )
}
