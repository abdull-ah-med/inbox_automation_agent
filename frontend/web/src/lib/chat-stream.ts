import type { ChatCitation } from "@/lib/types"

export type ChatStreamMeta = {
  type: "meta"
  citations: ChatCitation[]
  retrieval_count: number
  mailbox: string | null
  refused_write: boolean
}

export type ChatStreamEvent =
  | ChatStreamMeta
  | { type: "delta"; text: string }
  | { type: "done" }
  | { type: "error"; message: string }
  | { type: "status"; text: string }

export type ChatStreamHandlers = {
  onMeta?: (meta: ChatStreamMeta) => void
  onDelta?: (text: string) => void
  onDone?: () => void
  onError?: (message: string) => void
  onStatus?: (text: string) => void
}

export const parseChatStreamEvent = (block: string): ChatStreamEvent | null => {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart())
    .join("\n")
  if (!data) return null
  try {
    const parsed = JSON.parse(data) as { type?: unknown }
    if (parsed.type === "delta") {
      const text = (parsed as { text?: unknown }).text
      if (typeof text !== "string") return null
      return { type: "delta", text }
    }
    if (parsed.type === "done") {
      return { type: "done" }
    }
    if (parsed.type === "error") {
      const message = (parsed as { message?: unknown }).message
      if (typeof message !== "string") return null
      return { type: "error", message }
    }
    if (parsed.type === "status") {
      const text = (parsed as { text?: unknown }).text
      if (typeof text !== "string") return null
      return { type: "status", text }
    }
    if (parsed.type === "meta") {
      const meta = parsed as ChatStreamMeta
      return {
        type: "meta",
        citations: Array.isArray(meta.citations) ? meta.citations : [],
        retrieval_count:
          typeof meta.retrieval_count === "number" ? meta.retrieval_count : 0,
        mailbox: meta.mailbox ?? null,
        refused_write: Boolean(meta.refused_write),
      }
    }
    return null
  } catch {
    return null
  }
}

export const dispatchChatStreamBlock = (
  block: string,
  handlers: ChatStreamHandlers,
): "done" | "error" | "continue" => {
  const event = parseChatStreamEvent(block)
  if (event === null) return "continue"
  if (event.type === "meta") {
    handlers.onMeta?.(event)
    return "continue"
  }
  if (event.type === "delta") {
    handlers.onDelta?.(event.text)
    return "continue"
  }
  if (event.type === "error") {
    handlers.onError?.(event.message)
    return "error"
  }
  if (event.type === "status") {
    handlers.onStatus?.(event.text)
    return "continue"
  }
  handlers.onDone?.()
  return "done"
}
