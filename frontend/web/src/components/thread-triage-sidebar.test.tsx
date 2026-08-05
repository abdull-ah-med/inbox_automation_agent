import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ThreadTriageSidebar } from "@/components/thread-triage-sidebar"
import type { DraftView, ThreadSummary } from "@/lib/types"

const approveMock = vi.fn()
const rejectMock = vi.fn()
const markWrongMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    drafts: {
      approve: (...args: unknown[]) => approveMock(...args),
      reject: (...args: unknown[]) => rejectMock(...args),
      markWrong: (...args: unknown[]) => markWrongMock(...args),
    },
  },
}))

const thread: ThreadSummary = {
  id: "thread-1",
  mailbox: "elise@example.com",
  mailbox_key: "elise",
  subject: "Invoice question",
  state: "AWAITING_ACTION",
  urgency: "NORMAL",
  category: "billing",
  last_message_at: new Date().toISOString(),
  last_sender: "client@example.com",
  preview: "Need help",
  staleness_hours: 1,
  message_count: 2,
  has_draft: true,
  teaching_note: null,
  triage: null,
  outlook_url: null,
}

const baseDraft = (): DraftView => ({
  id: "draft-1",
  subject: "Re: Invoice question",
  body: "Happy to help with the invoice.",
  forward_to: null,
  teaching_note: "Acknowledge and resolve.",
  urgency: "NORMAL",
  urgency_reason: "Routine",
  created_at: new Date().toISOString(),
  suggested_actions: [],
  approved_at: null,
  rejected_at: null,
  edited_body: null,
  feedback_note: null,
  feedback_action: null,
  feedback_reason_code: null,
  routing_category: "billing",
  applied_skills: [],
  tool_calls: null,
})

const renderSidebar = (draft: DraftView | null = baseDraft()) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ThreadTriageSidebar
        threadId="thread-1"
        thread={thread}
        classification={null}
        draft={draft}
        triage={null}
        auditLog={[]}
      />
    </QueryClientProvider>,
  )
}

describe("ThreadTriageSidebar feedback buttons", () => {
  beforeEach(() => {
    approveMock.mockReset()
    rejectMock.mockReset()
    markWrongMock.mockReset()
    approveMock.mockResolvedValue(baseDraft())
    rejectMock.mockResolvedValue(baseDraft())
    markWrongMock.mockResolvedValue(baseDraft())
  })

  it("renders exactly two action buttons when a draft is present", () => {
    renderSidebar()
    expect(screen.getByRole("button", { name: "Approve draft" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reject draft" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Edit & Approve/i })).toBeNull()
    expect(screen.queryByRole("button", { name: /^Wrong$/i })).toBeNull()
  })

  it("opens approve dialog with draft body prefilled", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await user.click(screen.getByRole("button", { name: "Approve draft" }))
    const dialog = await screen.findByRole("dialog")
    const textarea = within(dialog).getByLabelText("Draft body to approve")
    expect(textarea).toHaveValue("Happy to help with the invoice.")
  })

  it("approving without edits calls approve with no body", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await user.click(screen.getByRole("button", { name: "Approve draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: "Confirm approve draft" }))
    await waitFor(() => {
      expect(approveMock).toHaveBeenCalledWith("draft-1", undefined)
    })
  })

  it("approving after editing sends edited_body", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await user.click(screen.getByRole("button", { name: "Approve draft" }))
    const dialog = await screen.findByRole("dialog")
    const textarea = within(dialog).getByLabelText("Draft body to approve")
    await user.clear(textarea)
    await user.type(textarea, "Revised reply body.")
    await user.click(within(dialog).getByRole("button", { name: "Confirm approve draft" }))
    await waitFor(() => {
      expect(approveMock).toHaveBeenCalledWith("draft-1", {
        edited_body: "Revised reply body.",
      })
    })
  })

  it("reject dialog requires reason and note", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    const confirm = within(dialog).getByRole("button", { name: "Confirm reject draft" })
    expect(confirm).toBeDisabled()
    await user.selectOptions(
      within(dialog).getByLabelText("Rejection reason"),
      "tone",
    )
    expect(confirm).toBeDisabled()
    await user.type(within(dialog).getByLabelText("Rejection note"), "Too curt")
    expect(confirm).toBeEnabled()
  })

  it("reject with wrong_action calls markWrong not reject", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.selectOptions(
      within(dialog).getByLabelText("Rejection reason"),
      "wrong_action",
    )
    await user.type(
      within(dialog).getByLabelText("Rejection note"),
      "No reply needed",
    )
    await user.click(within(dialog).getByRole("button", { name: "Confirm reject draft" }))
    await waitFor(() => {
      expect(markWrongMock).toHaveBeenCalledWith("draft-1", {
        feedback_note: "No reply needed",
        reason_code: "wrong_action",
      })
    })
    expect(rejectMock).not.toHaveBeenCalled()
  })

  it("reject with other reason calls reject", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.selectOptions(
      within(dialog).getByLabelText("Rejection reason"),
      "tone",
    )
    await user.type(within(dialog).getByLabelText("Rejection note"), "Tone off")
    await user.click(within(dialog).getByRole("button", { name: "Confirm reject draft" }))
    await waitFor(() => {
      expect(rejectMock).toHaveBeenCalledWith("draft-1", {
        feedback_note: "Tone off",
        reason_code: "tone",
      })
    })
    expect(markWrongMock).not.toHaveBeenCalled()
  })

  it("disables buttons and shows badge after feedback", () => {
    renderSidebar({
      ...baseDraft(),
      approved_at: new Date().toISOString(),
      feedback_action: "approve",
    })
    expect(screen.getByText("Approved")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Approve draft" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reject draft" })).toBeDisabled()
  })

  it("shows Marked as no reply needed badge for wrong feedback", () => {
    renderSidebar({
      ...baseDraft(),
      feedback_action: "wrong",
      feedback_note: "No reply",
    })
    expect(screen.getByText("Marked as no reply needed")).toBeInTheDocument()
  })

  it("shows empty skills message when no skills were applied", () => {
    renderSidebar()
    expect(screen.getByText("Skills used")).toBeInTheDocument()
    expect(
      screen.getByText("No skills applied for this draft"),
    ).toBeInTheDocument()
  })

  it("lists applied skills and successful reference reads", () => {
    const skillId = "skill-samplelab-1"
    renderSidebar({
      ...baseDraft(),
      applied_skills: [{ id: skillId, name: "samplelab-rebilling" }],
      tool_calls: [
        {
          skill_id: skillId,
          path: "references/client_rules.md",
          is_error: false,
        },
        {
          skill_id: skillId,
          path: "references/missing.md",
          is_error: true,
        },
      ],
    })
    expect(screen.getByText("samplelab-rebilling")).toBeInTheDocument()
    expect(screen.getByText("references/client_rules.md")).toBeInTheDocument()
    expect(screen.queryByText("references/missing.md")).toBeNull()
  })
})
