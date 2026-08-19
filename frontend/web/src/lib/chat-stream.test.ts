import { describe, expect, it, vi } from "vitest"

import { dispatchChatStreamBlock, parseChatStreamEvent } from "@/lib/chat-stream"

describe("parseChatStreamEvent", () => {
  it("reads a delta token from an SSE data line", () => {
    const event = parseChatStreamEvent('data: {"type":"delta","text":"The overdue "}')
    expect(event).toEqual({ type: "delta", text: "The overdue " })
  })

  it("reads meta citations without inventing a thread id", () => {
    const event = parseChatStreamEvent(
      'data: {"type":"meta","citations":[{"thread_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa","url_path":"/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}],"retrieval_count":1,"mailbox":"sales@example.com","refused_write":false}',
    )
    expect(event?.type).toBe("meta")
    if (event?.type !== "meta") return
    expect(event.retrieval_count).toBe(1)
    expect(event.citations[0]?.thread_id).toBe(
      "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    expect(event.citations[0]?.url_path).toBe(
      "/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
  })

  it("reads done and ignores a junk block", () => {
    expect(parseChatStreamEvent("data: {\"type\":\"done\"}")).toEqual({
      type: "done",
    })
    expect(parseChatStreamEvent(":")).toBeNull()
    expect(parseChatStreamEvent("data: not-json")).toBeNull()
  })

  it("reads a tool status line", () => {
    expect(parseChatStreamEvent('data: {"type":"status","text":"Searching mail"}')).toEqual(
      { type: "status", text: "Searching mail" },
    )
  })

  it("keeps the stream open when a tool status line arrives", () => {
    const onDone = vi.fn()
    const onStatus = vi.fn()
    const result = dispatchChatStreamBlock(
      'data: {"type":"status","text":"Searching mail"}',
      { onDone, onStatus },
    )
    expect(result).toBe("continue")
    expect(onStatus).toHaveBeenCalledWith("Searching mail")
    expect(onDone).not.toHaveBeenCalled()
  })
})
