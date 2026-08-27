import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { SentReplyPanel } from "@/components/sent-reply-panel"
import type { DraftView, SentReplyView } from "@/lib/types"

const sentReply: SentReplyView = {
  id: "sr-1",
  thread_id: "thread-1",
  message_id: "msg-1",
  draft_id: "draft-1",
  sent_body_snapshot: "Hello client\nExtra line",
  sent_at: "2026-07-09T14:00:00Z",
  matched_by: "approved_draft",
}

const draft: DraftView = {
  id: "draft-1",
  subject: "Re: Hello",
  body: "Hello client",
  teaching_note: "Note",
  urgency: null,
  urgency_reason: null,
  forward_to: null,
  created_at: "2026-07-09T12:00:00Z",
  suggested_actions: [],
  approved_at: null,
  rejected_at: null,
  edited_body: null,
  feedback_note: null,
  feedback_action: null,
  feedback_reason_code: null,
  routing_category: null,
  approval_note: null,
  approval_scope: null,
  applied_skills: [],
  tool_calls: null,
}

describe("SentReplyPanel", () => {
  it("renders proposed vs sent and resolved badge", () => {
    render(
      <SentReplyPanel
        sentReply={sentReply}
        draft={draft}
        diff={{ added: ["Extra line"], removed: [] }}
      />,
    )
    expect(screen.getByText("Resolved")).toBeInTheDocument()
    expect(screen.getByText(/Matched by: Approved draft/)).toBeInTheDocument()
    expect(screen.getByText("Proposed draft")).toBeInTheDocument()
    expect(screen.getByText("Extra line")).toBeInTheDocument()
  })

  it("notes that the sent reply was used for learning", () => {
    render(<SentReplyPanel sentReply={sentReply} draft={draft} diff={null} />)
    expect(screen.getByText(/This Outlook reply was saved for learning/)).toBeInTheDocument()
  })

  it("hides the learning note when the sent body is empty", () => {
    render(
      <SentReplyPanel
        sentReply={{ ...sentReply, sent_body_snapshot: "   " }}
        draft={draft}
        diff={null}
      />,
    )
    expect(screen.queryByText(/This Outlook reply was saved for learning/)).not.toBeInTheDocument()
  })

  it("collapses when hide is clicked", async () => {
    const user = userEvent.setup()
    render(<SentReplyPanel sentReply={sentReply} draft={draft} diff={null} />)
    await user.click(screen.getByRole("button", { name: "Collapse sent reply panel" }))
    expect(screen.queryByText("Proposed draft")).not.toBeInTheDocument()
  })

  it("skips full-body strike-through on substantial rewrites", () => {
    const proposed =
      "Line one of the draft\nLine two of the draft\nLine three of the draft\nLine four of the draft"
    const sent =
      "Completely different reply one\nCompletely different reply two\nCompletely different reply three\nCompletely different reply four"
    render(
      <SentReplyPanel
        sentReply={{ ...sentReply, sent_body_snapshot: sent }}
        draft={{ ...draft, body: proposed }}
        diff={{
          added: sent.split("\n"),
          removed: proposed.split("\n"),
        }}
      />,
    )
    expect(
      screen.queryByText(/substantially edited from the proposed draft/i),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/Showing the diff between proposed reply and actually sent/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/Line one of the draft/)).toBeInTheDocument()
    const struck = document.querySelector(".line-through")
    expect(struck).toBeNull()
  })

  it("notes when a line-level diff is shown", () => {
    render(
      <SentReplyPanel
        sentReply={sentReply}
        draft={draft}
        diff={{ added: ["Extra line"], removed: [] }}
      />,
    )
    expect(
      screen.getByText(/Showing the diff between proposed reply and actually sent/i),
    ).toBeInTheDocument()
  })

  it("omits the diff note when there is no diff", () => {
    render(<SentReplyPanel sentReply={sentReply} draft={draft} diff={null} />)
    expect(
      screen.queryByText(/Showing the diff between proposed reply and actually sent/i),
    ).not.toBeInTheDocument()
  })

  it("wraps long unbroken tokens in the sent reply column", () => {
    const longToken = "info@sample-services.example.com<mailto:info@sample-services.example.com>"
    render(
      <SentReplyPanel
        sentReply={{
          ...sentReply,
          sent_body_snapshot: `Thanks\n${longToken}\nhttps://orders.sample-services.example.com/MyAppLogin.cfm`,
        }}
        draft={draft}
        diff={null}
      />,
    )
    const token = screen.getByText((_, element) => {
      return (
        element?.textContent?.includes(longToken) === true &&
        element.classList.contains("break-words")
      )
    })
    expect(token).toBeTruthy()
    expect(token.closest(".overflow-hidden")).not.toBeNull()
  })
})
