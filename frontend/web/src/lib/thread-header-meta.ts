export type ThreadMessageParty = {
  direction: string
  sender: string
  to?: string[]
}

const ANGLE_EMAIL = /<([^<>@\s]+@[^<>@\s]+)>/

const normalizeAddress = (raw: string | null | undefined): string | null => {
  if (!raw) {
    return null
  }
  const text = raw.trim()
  if (!text || text.toLowerCase() === "unknown") {
    return null
  }
  const angled = ANGLE_EMAIL.exec(text)
  const value = (angled?.[1] ?? text).replace(/^["']+|["']+$/g, "").toLowerCase()
  return value.includes("@") ? value : null
}

const isSameAddress = (
  left: string | null | undefined,
  right: string | null | undefined,
): boolean => {
  const first = normalizeAddress(left)
  const second = normalizeAddress(right)
  return first != null && first === second
}

export const resolveThreadCounterpart = ({
  lastSender,
  mailbox,
  messages,
}: {
  lastSender: string | null
  mailbox?: string
  messages: ThreadMessageParty[]
}): string | null => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (
      message.direction === "inbound" &&
      message.sender.trim() &&
      !isSameAddress(message.sender, mailbox)
    ) {
      return message.sender.trim()
    }
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    for (const recipient of message.to ?? []) {
      if (recipient.trim() && !isSameAddress(recipient, mailbox)) {
        return recipient.trim()
      }
    }
  }
  if (lastSender && !isSameAddress(lastSender, mailbox)) {
    return lastSender
  }
  return lastSender
}

export const formatThreadHeaderMeta = ({
  mailbox,
  lastSender,
  messageCount,
  messages,
}: {
  mailbox: string
  lastSender: string | null
  messageCount: number
  messages: ThreadMessageParty[]
}): string => {
  const from = resolveThreadCounterpart({ lastSender, mailbox, messages }) ?? "Unknown sender"
  const countLabel = `${messageCount} ${messageCount === 1 ? "message" : "messages"}`
  return `From ${from} · ${countLabel} · Inbox ${mailbox}`
}
