import { describe, expect, it } from "vitest"

import {
  formatThreadHeaderMeta,
  resolveThreadCounterpart,
} from "@/lib/thread-header-meta"

describe("thread header meta", () => {
  it("prefers the latest inbound sender over an outbound last_sender", () => {
    expect(
      resolveThreadCounterpart({
        lastSender: "info@sample-services.example.com",
        messages: [
          {
            direction: "inbound",
            sender: "charity@example.com",
          },
          {
            direction: "outbound",
            sender: "info@sample-services.example.com",
          },
        ],
      }),
    ).toBe("charity@example.com")
  })

  it("falls back to last_sender when there is no inbound message", () => {
    expect(
      resolveThreadCounterpart({
        lastSender: "info@sample-services.example.com",
        messages: [
          {
            direction: "outbound",
            sender: "info@sample-services.example.com",
          },
        ],
      }),
    ).toBe("info@sample-services.example.com")
  })

  it("labels sender and mailbox so outbound replies do not look like a duplicate mystery", () => {
    expect(
      formatThreadHeaderMeta({
        mailbox: "info@sample-services.example.com",
        lastSender: "info@sample-services.example.com",
        messageCount: 6,
        messages: [
          {
            direction: "inbound",
            sender: "charity@example.com",
          },
          {
            direction: "outbound",
            sender: "info@sample-services.example.com",
          },
        ],
      }),
    ).toBe(
      "From charity@example.com · 6 messages · Inbox info@sample-services.example.com",
    )
  })
})
