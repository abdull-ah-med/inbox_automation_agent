import { describe, expect, it } from "vitest"

import { buildThreadStory } from "@/lib/thread-story"
import type { MessageDetail } from "@/lib/types"

const message = (
  overrides: Partial<MessageDetail> &
    Pick<MessageDetail, "id" | "direction" | "sender" | "received_at">,
): MessageDetail => ({
  to: [],
  cc: [],
  bcc: [],
  body_text: "",
  reply_text: "",
  body_preview: null,
  has_attachments: false,
  outlook_url: null,
  sender_name: null,
  sender_salute_name: null,
  ...overrides,
})

describe("buildThreadStory salute names", () => {
  it("uses salute names and skips Graph role labels and email locals", () => {
    const events = buildThreadStory({
      messages: [
        message({
          id: "m1",
          direction: "inbound",
          sender: "Dev@sample-site.example.com",
          sender_name: "Sample Developer",
          sender_salute_name: null,
          to: ["sampleagent@sample-site.example.com"],
          received_at: "2026-09-04T12:39:00Z",
        }),
        message({
          id: "m2",
          direction: "outbound",
          sender: "sampleagent@sample-site.example.com",
          sender_name: "SampleSite Support",
          sender_salute_name: "Elise",
          to: ["Dev@sample-site.example.com"],
          received_at: "2026-09-03T18:31:00Z",
        }),
      ],
    })

    expect(events.map((event) => event.headline)).toEqual(["Elise wrote", "Someone wrote to Elise"])
    expect(events.some((event) => event.headline.includes("Sample"))).toBe(false)
    expect(events.some((event) => event.headline.includes("Dev"))).toBe(false)
  })

  it("uses the mailbox owner when no outbound salute exists yet", () => {
    const events = buildThreadStory({
      mailboxOwner: "Elise",
      messages: [
        message({
          id: "m1",
          direction: "inbound",
          sender: "jordan@sample-screening.example.com",
          sender_name: "Jordan Morgan",
          sender_salute_name: "Jordan",
          to: ["sampleagent@sample-site.example.com"],
          received_at: "2026-09-04T15:15:00Z",
        }),
      ],
    })

    expect(events.map((event) => event.headline)).toEqual(["Jordan wrote to Elise"])
  })

  it("omits the counterparty when the mailbox owner is unknown", () => {
    const events = buildThreadStory({
      messages: [
        message({
          id: "m1",
          direction: "inbound",
          sender: "jordan@sample-screening.example.com",
          sender_salute_name: "Jordan",
          received_at: "2026-09-04T15:15:00Z",
        }),
      ],
    })

    expect(events.map((event) => event.headline)).toEqual(["Jordan wrote"])
    expect(events.some((event) => event.headline.includes("We"))).toBe(false)
  })

  it("uses a signed or taught salute name for the role mailbox", () => {
    const events = buildThreadStory({
      messages: [
        message({
          id: "m1",
          direction: "inbound",
          sender: "Dev@sample-site.example.com",
          sender_name: "Sample Developer",
          sender_salute_name: "Divyansh",
          to: ["sampleagent@sample-site.example.com"],
          received_at: "2026-09-03T18:31:00Z",
        }),
        message({
          id: "m2",
          direction: "outbound",
          sender: "sampleagent@sample-site.example.com",
          sender_salute_name: "Elise",
          to: ["Dev@sample-site.example.com"],
          received_at: "2026-09-04T12:39:00Z",
        }),
      ],
    })

    expect(events.map((event) => event.headline)).toEqual([
      "Divyansh wrote to Elise",
      "Elise replied to Divyansh",
    ])
  })
})
