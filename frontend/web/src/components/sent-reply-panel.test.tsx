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

  it("collapses when hide is clicked", async () => {
    const user = userEvent.setup()
    render(
      <SentReplyPanel sentReply={sentReply} draft={draft} diff={null} />,
    )
    await user.click(screen.getByRole("button", { name: "Collapse sent reply panel" }))
    expect(screen.queryByText("Proposed draft")).not.toBeInTheDocument()
  })
})
