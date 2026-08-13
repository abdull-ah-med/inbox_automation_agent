import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

type BadgeTone = "neutral" | "blue" | "green" | "amber" | "red" | "purple"

const TONE_CLASS: Record<BadgeTone, string> = {
  neutral: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  blue: "bg-blue-100 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300",
  green: "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
  red: "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  purple: "bg-purple-100 text-purple-700 dark:bg-purple-950/40 dark:text-purple-300",
}

export const StatusBadge = ({
  label,
  tone = "neutral",
  className,
}: {
  label: string
  tone?: BadgeTone
  className?: string
}) => {
  return (
    <Badge
      variant="secondary"
      className={cn(
        "rounded-md border-0 leading-none",
        TONE_CLASS[tone],
        className,
      )}
    >
      {label}
    </Badge>
  )
}

export const urgencyTone = (urgency: string | null | undefined): BadgeTone => {
  if (!urgency) return "neutral"
  const value = urgency.toUpperCase()
  if (value === "CRITICAL") return "red"
  if (value === "HIGH") return "amber"
  if (value === "NORMAL") return "blue"
  if (value === "LOW") return "green"
  return "neutral"
}

/** Tone + human label for a thread's pipeline state (see backend ThreadStateEnum). */
export const stateTone = (state: string | null | undefined): BadgeTone => {
  switch (state) {
    case "DRAFTED":
      return "green"
    case "REQUIRES_HUMAN":
      return "red"
    case "SPAM":
      return "neutral"
    case "NO_ACTION":
      return "neutral"
    case "AWAITING_CLIENT":
    case "AWAITING_VENDOR":
    case "AWAITING_PARTNER":
      return "blue"
    case "RESOLVED":
      return "green"
    case "NEW":
      return "amber"
    default:
      return "neutral"
  }
}

export const stateLabel = (state: string | null | undefined): string => {
  switch (state) {
    case "NEW":
      return "New — not yet triaged"
    case "NO_ACTION":
      return "No action needed"
    case "REQUIRES_HUMAN":
      return "Needs human review"
    case "AWAITING_CLIENT":
      return "Awaiting client"
    case "AWAITING_VENDOR":
      return "Awaiting vendor"
    case "AWAITING_PARTNER":
      return "Awaiting partner"
    case "DRAFTED":
      return "Draft ready"
    case "SPAM":
      return "Spam"
    case "RESOLVED":
      return "Resolved"
    default:
      return state ?? "—"
  }
}
