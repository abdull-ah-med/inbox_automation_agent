export type ThreadMessageParty = {
  direction: string
  sender: string
}

export const resolveThreadCounterpart = ({
  lastSender,
  messages,
}: {
  lastSender: string | null
  messages: ThreadMessageParty[]
}): string | null => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.direction === "inbound" && message.sender.trim()) {
      return message.sender.trim()
    }
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
  const from = resolveThreadCounterpart({ lastSender, messages }) ?? "Unknown sender"
  const countLabel = `${messageCount} ${messageCount === 1 ? "message" : "messages"}`
  return `From ${from} · ${countLabel} · Inbox ${mailbox}`
}
