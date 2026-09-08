import type { MessageDetail } from "@/lib/types"

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

const saluteName = (message: MessageDetail): string | null => {
  const name = message.sender_salute_name?.trim()
  return name ? name : null
}

const ownerName = (messages: MessageDetail[], mailboxOwner?: string | null): string | null => {
  const namedOutbound = messages.find(
    (message) => message.direction === "outbound" && saluteName(message),
  )
  if (namedOutbound) return saluteName(namedOutbound)
  const owner = mailboxOwner?.trim()
  return owner ? owner : null
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
  ours: string | null,
): string | null => {
  if (message.direction === "outbound") {
    const lastInbound = prior.findLast((row) => row.direction === "inbound")
    if (lastInbound) return saluteName(lastInbound)
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
  mailboxOwner = null,
  facts = [],
}: {
  messages: MessageDetail[]
  mailboxOwner?: string | null
  facts?: { body: string; source_message_id: string | null }[]
}): ThreadStoryEvent[] => {
  const ordered = messages.toSorted(
    (a, b) => new Date(a.received_at).getTime() - new Date(b.received_at).getTime(),
  )
  const ours = ownerName(ordered, mailboxOwner)
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
      saluteName(message) ?? (message.direction === "outbound" ? (ours ?? "Someone") : "Someone")
    const hasPriorInbound = prior.some((row) => row.direction === "inbound")
    const meeting = message.meeting_message_type ? MEETING_VERB[message.meeting_message_type] : null
    let headline: string
    if (meeting) {
      headline = `${from} ${meeting}`
    } else {
      const to = counterpartyFor(message, prior, ours)
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
