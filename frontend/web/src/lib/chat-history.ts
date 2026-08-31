import type { ChatHistoryTurn } from "@/lib/types"

export const CHAT_HISTORY_MAX_TURNS = 20
export const CHAT_HISTORY_CONTENT_MAX_CHARS = 8_000

export const capChatHistory = <T>(turns: T[]): T[] => {
  if (turns.length <= CHAT_HISTORY_MAX_TURNS) {
    return turns
  }
  return turns.slice(-CHAT_HISTORY_MAX_TURNS)
}

export const toChatHistoryPayload = (turns: ChatHistoryTurn[]): ChatHistoryTurn[] => {
  return capChatHistory(turns).map((turn) => {
    if (turn.content.length <= CHAT_HISTORY_CONTENT_MAX_CHARS) {
      return turn
    }
    return {
      ...turn,
      content: turn.content.slice(0, CHAT_HISTORY_CONTENT_MAX_CHARS),
    }
  })
}
