import type { MessageDetail } from "@/lib/types"

const GENERIC_LOCAL = new Set([
  "info",
  "hello",
  "support",
  "sales",
  "mail",
  "noreply",
  "no-reply",
  "donotreply",
  "notifications",
])

const MEETING_VERB: Record<string, string> = {
  meetingAccepted: "accepted the meeting",
  meetingTenativelyAccepted: "tentatively accepted the meeting",
  meetingDeclined: "declined the meeting",
  meetingCancelled: "cancelled the meeting",
  meetingRequest: "sent a meeting request",
}

export type ThreadStoryEvent = {
  id: string
  headline: string
  whatHappened: string | null
  occurredAt: string
  direction: "inbound" | "outbound"
}

const normalizeEmail = (value: string): string => value.trim().toLowerCase()

export const firstNameFromDisplay = (fullName: string | null | undefined): string | null => {
  if (!fullName?.trim()) return null
  const cleaned = fullName.trim().replace(/\s+/g, " ")
  if (/^\d/.test(cleaned)) return null
  if (cleaned.includes(",")) {
    const after = cleaned.split(",")[1]?.trim().split(/\s+/)[0] ?? ""
    return /^[A-Za-z]+$/.test(after) ? after : null
  }
  const parts = cleaned.split(" ")
  if (parts.length === 1 && /^[A-Za-z]+$/.test(parts[0])) return parts[0]
  if (
    parts.length >= 2 &&
    parts.length <= 3 &&
    parts.every((part) => /^[A-Za-z]+$/.test(part.replace(".", "")))
  ) {
    return parts[0]
  }
  return null
}

export const firstNameFromEmail = (address: string | null | undefined): string | null => {
  if (!address?.includes("@")) return null
  const local = address.split("@")[0]?.trim().toLowerCase() ?? ""
  if (!local || local.includes("-") || GENERIC_LOCAL.has(local)) return null
  const token = local.split(/[._+]/)[0] ?? ""
  if (token.length < 2 || !/^[a-z]+$/.test(token)) return null
  return token.charAt(0).toUpperCase() + token.slice(1)
}

const personName = (displayName: string | null | undefined, email: string): string | null => {
  return firstNameFromDisplay(displayName) ?? firstNameFromEmail(email)
}

const ownerName = (mailbox: string, messages: MessageDetail[]): string => {
  const namedOutbound = messages.find(
    (message) => message.direction === "outbound" && firstNameFromDisplay(message.sender_name),
  )
  if (namedOutbound) return firstNameFromDisplay(namedOutbound.sender_name) as string
  return firstNameFromEmail(mailbox) ?? "We"
}

const whatHappened = (
  message: MessageDetail,
  factsByMessageId: Map<string, string[]>,
): string | null => {
  const one = message.summary_one_line?.trim()
  if (one) return one.replace(/[.]+$/, "")
  const facts = factsByMessageId.get(message.id)
  if (facts?.length) return facts.join("; ")
  const ask = message.summary_ask?.trim()
  if (ask) return `Asked for ${ask.replace(/[.]+$/, "")}`
  const intent = message.summary_intent?.trim()
  if (intent) return intent.replace(/[.]+$/, "")
  return null
}

const counterpartyFor = (
  message: MessageDetail,
  prior: MessageDetail[],
  mailbox: string,
  ours: string,
): string | null => {
  if (message.direction === "outbound") {
    const lastInbound = prior.findLast((row) => row.direction === "inbound")
    if (lastInbound) return personName(lastInbound.sender_name, lastInbound.sender)
    const otherTo = message.to.find(
      (address) => normalizeEmail(address) !== normalizeEmail(mailbox),
    )
    if (otherTo) return firstNameFromEmail(otherTo)
    return null
  }
  return ours
}

const verbFor = (message: MessageDetail, hasPriorInbound: boolean): string => {
  const meeting = message.meeting_message_type ? MEETING_VERB[message.meeting_message_type] : null
  if (meeting) return meeting
  if (message.direction === "outbound") return hasPriorInbound ? "replied to" : "wrote to"
  return "wrote to"
}

export const buildThreadStory = ({
  messages,
  mailbox,
  facts = [],
}: {
  messages: MessageDetail[]
  mailbox: string
  subject?: string | null
  facts?: { body: string; source_message_id: string | null }[]
}): ThreadStoryEvent[] => {
  const ordered = messages.toSorted(
    (a, b) => new Date(a.received_at).getTime() - new Date(b.received_at).getTime(),
  )
  const ours = ownerName(mailbox, ordered)
  const events: ThreadStoryEvent[] = []
  const prior: MessageDetail[] = []
  const factsByMessageId = new Map<string, string[]>()
  for (const fact of facts) {
    const source = fact.source_message_id
    const body = fact.body.trim()
    if (!source || !body) continue
    const existing = factsByMessageId.get(source) ?? []
    existing.push(body)
    factsByMessageId.set(source, existing)
  }

  for (const message of ordered) {
    const from =
      firstNameFromDisplay(message.sender_name) ??
      (message.direction === "outbound" ? ours : (firstNameFromEmail(message.sender) ?? "Someone"))
    const hasPriorInbound = prior.some((row) => row.direction === "inbound")
    const meeting = message.meeting_message_type ? MEETING_VERB[message.meeting_message_type] : null
    let headline: string
    if (meeting) {
      headline = `${from} ${meeting}`
    } else {
      const to = counterpartyFor(message, prior, mailbox, ours)
      const verb = verbFor(message, hasPriorInbound)
      headline = to ? `${from} ${verb} ${to}` : `${from} wrote`
    }
    events.push({
      id: message.id,
      headline,
      whatHappened: meeting ? null : whatHappened(message, factsByMessageId),
      occurredAt: message.received_at,
      direction: message.direction === "outbound" ? "outbound" : "inbound",
    })
    prior.push(message)
  }

  return events
}
