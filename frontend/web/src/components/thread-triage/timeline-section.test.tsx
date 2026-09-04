import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { TimelineSection } from "@/components/thread-triage/timeline-section"
import type { MessageDetail } from "@/lib/types"
import { renderWithProviders } from "@/test/render"

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      getContext: () => Promise.resolve({ facts: [] }),
    },
  },
}))

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
  ...overrides,
})

describe("TimelineSection", () => {
  it("tells the email story with names and the actual send date", () => {
    renderWithProviders(
      <TimelineSection
        threadId="thread-1"
        mailbox="elise@sample-site.example.com"
        subject="Re: SampleClient packet"
        messages={[
          message({
            id: "m1",
            direction: "inbound",
            sender: "alex@hospital.org",
            sender_name: "Alex Patel",
            to: ["elise@sample-site.example.com"],
            reply_text: "Hi Elise,\nCan you send the SampleClient packet today?\nThanks,\nAnita",
            summary_one_line: "Alex asked Elise to send the SampleClient packet today",
            received_at: "2026-08-27T14:14:00Z",
          }),
          message({
            id: "m2",
            direction: "outbound",
            sender: "elise@sample-site.example.com",
            sender_name: "Elise Chouest",
            to: ["alex@hospital.org"],
            reply_text: "Hi Alex,\nSending the packet this afternoon.\nElise",
            summary_one_line: "Elise said she would send the packet that afternoon",
            received_at: "2026-08-27T16:02:00Z",
          }),
        ]}
      />,
    )

    expect(screen.getByText("Alex wrote to Elise")).toBeInTheDocument()
    expect(
      screen.getByText("Alex asked Elise to send the SampleClient packet today"),
    ).toBeInTheDocument()
    expect(screen.getByText("Elise replied to Alex")).toBeInTheDocument()
    expect(
      screen.getByText("Elise said she would send the packet that afternoon"),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Hi Elise/)).not.toBeInTheDocument()
    expect(screen.getAllByText(/8\/27\/2026/).length).toBeGreaterThanOrEqual(2)
    const items = screen.getAllByRole("listitem").map((item) => item.textContent ?? "")
    expect(items[0]).toContain("Elise replied to Alex")
    expect(items[1]).toContain("Alex wrote to Elise")
  })

  it("shows empty copy when there are no emails", () => {
    renderWithProviders(
      <TimelineSection
        threadId="thread-1"
        mailbox="elise@sample-site.example.com"
        subject="Invoice"
        messages={[]}
      />,
    )
    expect(screen.getByText("No emails on this thread yet.")).toBeInTheDocument()
  })

  it("folds emails older than the latest seven behind a show control", async () => {
    const user = userEvent.setup()
    const messages = Array.from({ length: 8 }, (_, index) =>
      message({
        id: `m${index}`,
        direction: index % 2 === 0 ? "inbound" : "outbound",
        sender: index % 2 === 0 ? `p${index}@hospital.org` : "elise@sample-site.example.com",
        sender_name: index % 2 === 0 ? `Pat${index}` : "Elise Chouest",
        to: index % 2 === 0 ? ["elise@sample-site.example.com"] : [`p${index}@hospital.org`],
        summary_one_line: `What happened in email ${index}`,
        received_at: `2026-08-0${index + 1}T12:00:00Z`,
      }),
    )

    renderWithProviders(
      <TimelineSection
        threadId="thread-1"
        mailbox="elise@sample-site.example.com"
        subject="Packet"
        messages={messages}
      />,
    )

    expect(screen.queryByText("What happened in email 0")).not.toBeInTheDocument()
    expect(screen.getByText("What happened in email 1")).toBeInTheDocument()
    expect(screen.getByText("What happened in email 7")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Show 1 earlier email" }))
    expect(screen.getByText("What happened in email 0")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Hide earlier emails" }))
    expect(screen.queryByText("What happened in email 0")).not.toBeInTheDocument()
  })
})
