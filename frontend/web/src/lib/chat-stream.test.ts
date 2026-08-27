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
    expect(event.citations[0]?.thread_id).toBe("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    expect(event.citations[0]?.url_path).toBe("/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
  })

  it("reads done and ignores a junk block", () => {
    expect(parseChatStreamEvent('data: {"type":"done"}')).toEqual({
      type: "done",
    })
    expect(parseChatStreamEvent(":")).toBeNull()
    expect(parseChatStreamEvent("data: not-json")).toBeNull()
  })

  it("reads the H2 fail-closed UNKNOWN verdict off the terminal done event", () => {
    // H2: verify_grounded timeouts / parse failures now return UNKNOWN
    // instead of a silent SUPPORTED — the client must be able to parse it.
    expect(parseChatStreamEvent('data: {"type":"done","grounded_verifier":"UNKNOWN"}')).toEqual({
      type: "done",
      grounded_verifier: "UNKNOWN",
    })
  })

  it("reads grounded_verifier off the terminal done event", () => {
    // H1: the backend now sends the verdict once, on "done", instead of a
    // second "meta" event — the client must read it from here.
    expect(parseChatStreamEvent('data: {"type":"done","grounded_verifier":"UNSUPPORTED"}')).toEqual(
      { type: "done", grounded_verifier: "UNSUPPORTED" },
    )
  })

  it("drops an invalid grounded_verifier value on done instead of trusting it", () => {
    expect(parseChatStreamEvent('data: {"type":"done","grounded_verifier":"bogus"}')).toEqual({
      type: "done",
    })
  })

  it("reads a tool status line", () => {
    expect(parseChatStreamEvent('data: {"type":"status","text":"Searching mail"}')).toEqual({
      type: "status",
      text: "Searching mail",
    })
  })

  it("keeps the stream open when a tool status line arrives", () => {
    const onDone = vi.fn()
    const onStatus = vi.fn()
    const result = dispatchChatStreamBlock('data: {"type":"status","text":"Searching mail"}', {
      onDone,
      onStatus,
    })
    expect(result).toBe("continue")
    expect(onStatus).toHaveBeenCalledWith("Searching mail")
    expect(onDone).not.toHaveBeenCalled()
  })

  it("passes the done event's grounded_verifier through to onDone", () => {
    const onDone = vi.fn()
    const result = dispatchChatStreamBlock(
      'data: {"type":"done","grounded_verifier":"SUPPORTED"}',
      { onDone },
    )
    expect(result).toBe("done")
    expect(onDone).toHaveBeenCalledWith("SUPPORTED")
  })

  it("passes undefined to onDone when the done event carries no verdict", () => {
    const onDone = vi.fn()
    dispatchChatStreamBlock('data: {"type":"done"}', { onDone })
    expect(onDone).toHaveBeenCalledWith(undefined)
  })
})
