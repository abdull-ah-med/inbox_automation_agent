import { describe, expect, it } from "vitest"

import { formatThreadHeaderMeta, resolveThreadCounterpart } from "@/lib/thread-header-meta"

describe("thread header meta", () => {
  it("prefers the latest inbound sender over an outbound last_sender", () => {
    expect(
      resolveThreadCounterpart({
        mailbox: "info@sample-services.example.com",
        lastSender: "info@sample-services.example.com",
        messages: [
          {
            direction: "inbound",
            sender: "charity@example.com",
          },
          {
            direction: "outbound",
            sender: "info@sample-services.example.com",
            to: ["charity@example.com"],
          },
        ],
      }),
    ).toBe("charity@example.com")
  })

  it("uses the outbound To address when the only message is from the mailbox", () => {
    expect(
      resolveThreadCounterpart({
        mailbox: "info@sample-services.example.com",
        lastSender: "info@sample-services.example.com",
        messages: [
          {
            direction: "outbound",
            sender: "info@sample-services.example.com",
            to: ["nealdavien@yahoo.com"],
          },
        ],
      }),
    ).toBe("nealdavien@yahoo.com")
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
    ).toBe("From charity@example.com · 6 messages · Inbox info@sample-services.example.com")
  })

  it("labels an outbound-only thread with the recipient, not the mailbox", () => {
    expect(
      formatThreadHeaderMeta({
        mailbox: "info@sample-services.example.com",
        lastSender: "info@sample-services.example.com",
        messageCount: 1,
        messages: [
          {
            direction: "outbound",
            sender: "info@sample-services.example.com",
            to: ["nealdavien@yahoo.com"],
          },
        ],
      }),
    ).toBe("From nealdavien@yahoo.com · 1 message · Inbox info@sample-services.example.com")
  })
})
