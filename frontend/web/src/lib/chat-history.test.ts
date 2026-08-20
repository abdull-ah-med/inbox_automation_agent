import { describe, expect, it } from "vitest"

import {
  CHAT_HISTORY_CONTENT_MAX_CHARS,
  CHAT_HISTORY_MAX_TURNS,
  capChatHistory,
  toChatHistoryPayload,
} from "@/lib/chat-history"

describe("capChatHistory", () => {
  it("keeps the newest twenty turns and drops the oldest", () => {
    const turns = Array.from({ length: 21 }, (_, index) => `turn-${index}`)
    expect(CHAT_HISTORY_MAX_TURNS).toBe(20)
    expect(capChatHistory(turns)).toEqual(
      Array.from({ length: 20 }, (_, index) => `turn-${index + 1}`),
    )
  })

  it("leaves short conversations unchanged", () => {
    expect(capChatHistory(["hello", "there"])).toEqual(["hello", "there"])
  })
})

describe("toChatHistoryPayload", () => {
  it("truncates an assistant answer longer than the history budget", () => {
    const answer = "Latest on O'Mason. " + "n".repeat(CHAT_HISTORY_CONTENT_MAX_CHARS)
    const payload = toChatHistoryPayload([
      { role: "user", content: "give me the latest on omason" },
      { role: "assistant", content: answer },
    ])
    expect(payload[1]?.content).toHaveLength(CHAT_HISTORY_CONTENT_MAX_CHARS)
    expect(payload[1]?.content.startsWith("Latest on O'Mason.")).toBe(true)
  })
})
