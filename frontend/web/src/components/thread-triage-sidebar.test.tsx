import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ThreadTriageSidebar } from "@/components/thread-triage-sidebar"
import { ApiError } from "@/lib/api/client"
import type { DraftView, ThreadSummary } from "@/lib/types"

const approveMock = vi.fn()
const rejectMock = vi.fn()
const relatedMock = vi.fn()
const markWrongMock = vi.fn()
const getContextMock = vi.fn()

const markNotSpamMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    drafts: {
      approve: (...args: unknown[]) => approveMock(...args),
      reject: (...args: unknown[]) => rejectMock(...args),
      markWrong: (...args: unknown[]) => markWrongMock(...args),
    },
    threads: {
      related: (...args: unknown[]) => relatedMock(...args),
      markNotSpam: (...args: unknown[]) => markNotSpamMock(...args),
      getContext: (...args: unknown[]) => getContextMock(...args),
      rebuildContext: vi.fn(),
      discardContextFact: vi.fn(),
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
  urgency_reason: null,
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
  approval_note: null,
  approval_scope: null,
  applied_skills: [],
  tool_calls: null,
})

beforeEach(() => {
  getContextMock.mockReset()
  getContextMock.mockRejectedValue(new ApiError("Not found", 404))
})

const renderSidebar = (draft: DraftView | null = baseDraft()) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(["dashboard", "overview"], { total_awaiting: 4 })
  client.setQueryData(["mailbox", "elise@example.com", "threads"], { items: [] })
  const view = render(
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
  return { client, ...view }
}

const openDraftTab = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole("tab", { name: "Draft" }))
}

describe("ThreadTriageSidebar feedback buttons", () => {
  beforeEach(() => {
    approveMock.mockReset()
    rejectMock.mockReset()
    markWrongMock.mockReset()
    relatedMock.mockReset()
    getContextMock.mockReset()
    approveMock.mockResolvedValue(baseDraft())
    rejectMock.mockResolvedValue(baseDraft())
    markWrongMock.mockResolvedValue(baseDraft())
    relatedMock.mockResolvedValue({ items: [] })
    getContextMock.mockRejectedValue(new ApiError("Not found", 404))
  })

  it("renders exactly two action buttons when a draft is present", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await openDraftTab(user)
    expect(screen.getByRole("button", { name: "Approve draft" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reject draft" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Edit & Approve/i })).toBeNull()
    expect(screen.queryByRole("button", { name: /^Wrong$/i })).toBeNull()
  })

  it("opens approve dialog with draft body prefilled", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Approve draft" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveAttribute("data-size", "lg")
    expect(dialog.className).toContain("sm:max-w-2xl")
    const textarea = within(dialog).getByLabelText("Draft body to approve")
    expect(textarea).toHaveValue("Happy to help with the invoice.")
  })

  it("opens reject dialog at lg size", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveAttribute("data-size", "lg")
    expect(dialog.className).toContain("sm:max-w-2xl")
  })

  it("approving without edits calls approve with no body", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await openDraftTab(user)
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
    await openDraftTab(user)
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

  it("disables approve when learning note lacks scope", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Approve draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.type(within(dialog).getByLabelText("Approval learning note"), "Soften the tone")
    const confirm = within(dialog).getByRole("button", { name: "Confirm approve draft" })
    expect(confirm).toBeDisabled()
    expect(within(dialog).getByRole("status")).toHaveTextContent(
      "Choose a scope when providing a learning note.",
    )
  })

  it("approving with learning note and similar scope sends both fields", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Approve draft" }))
    const dialog = await screen.findByRole("dialog")
    const textarea = within(dialog).getByLabelText("Draft body to approve")
    await user.clear(textarea)
    await user.type(textarea, "Revised reply body.")
    await user.type(
      within(dialog).getByLabelText("Approval learning note"),
      "Lead with invoice number",
    )
    await user.click(
      within(dialog).getByRole("radio", { name: "Apply learning to similar emails" }),
    )
    await user.click(within(dialog).getByRole("button", { name: "Confirm approve draft" }))
    await waitFor(() => {
      expect(approveMock).toHaveBeenCalledWith("draft-1", {
        edited_body: "Revised reply body.",
        approval_note: "Lead with invoice number",
        approval_scope: "similar",
      })
    })
  })

  it("approving with once scope sends once without requiring body edit", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Approve draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.type(within(dialog).getByLabelText("Approval learning note"), "One-off exception")
    await user.click(
      within(dialog).getByRole("radio", {
        name: "Apply learning to this thread only",
      }),
    )
    await user.click(within(dialog).getByRole("button", { name: "Confirm approve draft" }))
    await waitFor(() => {
      expect(approveMock).toHaveBeenCalledWith("draft-1", {
        approval_note: "One-off exception",
        approval_scope: "once",
      })
    })
  })

  it("reject dialog requires reason and note", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    const confirm = within(dialog).getByRole("button", { name: "Confirm reject draft" })
    expect(confirm).toBeDisabled()
    await user.click(within(dialog).getByRole("combobox", { name: "Rejection reason" }))
    await user.click(await screen.findByRole("option", { name: "Tone off" }))
    expect(confirm).toBeDisabled()
    await user.type(within(dialog).getByLabelText("Rejection note"), "Too curt")
    expect(confirm).toBeEnabled()
  })

  it("reject with wrong_action calls markWrong not reject", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("combobox", { name: "Rejection reason" }))
    await user.click(await screen.findByRole("option", { name: "Wrong action / no reply needed" }))
    await user.type(within(dialog).getByLabelText("Rejection note"), "No reply needed")
    await user.click(within(dialog).getByRole("button", { name: "Confirm reject draft" }))
    await waitFor(() => {
      expect(markWrongMock).toHaveBeenCalledWith("draft-1", {
        feedback_note: "No reply needed",
        reason_code: "wrong_action",
      })
    })
    expect(rejectMock).not.toHaveBeenCalled()
  })

  it("opens apply-siblings dialog after no-reply when related threads exist", async () => {
    relatedMock.mockResolvedValue({
      items: [
        {
          thread_id: "sib-sampleclient",
          mailbox: "sales@example.com",
          subject: "SampleClient follow-up 8/14",
          sender: "rep@sample-client.example.com",
          last_message_at: "2026-08-14T15:00:00Z",
          urgency: "NORMAL",
          score: 0.02,
          status: "proposed",
        },
      ],
    })
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const rejectDialog = await screen.findByRole("dialog")
    await user.click(within(rejectDialog).getByRole("combobox", { name: "Rejection reason" }))
    await user.click(await screen.findByRole("option", { name: "Wrong action / no reply needed" }))
    await user.type(within(rejectDialog).getByLabelText("Rejection note"), "No reply needed")
    await user.click(within(rejectDialog).getByRole("button", { name: "Confirm reject draft" }))
    const siblingDialog = await screen.findByRole("dialog")
    expect(siblingDialog).toHaveTextContent("Apply no reply")
    expect(siblingDialog).toHaveTextContent("SampleClient follow-up 8/14")
  })

  it("explains that no-reply leaves Needs Attention", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("leaves Needs Attention")
  })

  it("refreshes dashboard and mailbox queues after no-reply", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    const { client } = renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("combobox", { name: "Rejection reason" }))
    await user.click(await screen.findByRole("option", { name: "Wrong action / no reply needed" }))
    await user.type(within(dialog).getByLabelText("Rejection note"), "No reply needed")
    await user.click(within(dialog).getByRole("button", { name: "Confirm reject draft" }))
    await waitFor(() => {
      expect(markWrongMock).toHaveBeenCalled()
    })
    expect(client.getQueryState(["dashboard", "overview"])?.isInvalidated).toBe(true)
    expect(client.getQueryState(["mailbox", "elise@example.com", "threads"])?.isInvalidated).toBe(
      true,
    )
  })

  it("refreshes dashboard after approve so the thread leaves Needs Attention", async () => {
    const user = userEvent.setup()
    const { client } = renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Approve draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: "Confirm approve draft" }))
    await waitFor(() => {
      expect(approveMock).toHaveBeenCalled()
    })
    expect(client.getQueryState(["dashboard", "overview"])?.isInvalidated).toBe(true)
    expect(client.getQueryState(["mailbox", "elise@example.com", "threads"])?.isInvalidated).toBe(
      true,
    )
  })

  it("reject with other reason calls reject", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderSidebar()
    await openDraftTab(user)
    await user.click(screen.getByRole("button", { name: "Reject draft" }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("combobox", { name: "Rejection reason" }))
    await user.click(await screen.findByRole("option", { name: "Tone off" }))
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

  it("disables buttons and shows badge after feedback", async () => {
    const user = userEvent.setup()
    renderSidebar({
      ...baseDraft(),
      approved_at: new Date().toISOString(),
      feedback_action: "approve",
    })
    await openDraftTab(user)
    expect(screen.getByText("Approved")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Approve draft" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reject draft" })).toBeDisabled()
  })

  it("shows Marked as no reply needed badge for wrong feedback", async () => {
    const user = userEvent.setup()
    renderSidebar({
      ...baseDraft(),
      feedback_action: "wrong",
      feedback_note: "No reply",
    })
    await openDraftTab(user)
    expect(screen.getByText("Marked as no reply needed")).toBeInTheDocument()
  })

  it("shows empty skills message when no skills were applied", async () => {
    const user = userEvent.setup()
    renderSidebar()
    await openDraftTab(user)
    expect(screen.getByText("Skills used")).toBeInTheDocument()
    expect(screen.getByText("No skills applied for this draft")).toBeInTheDocument()
  })

  it("lists applied skills and successful reference reads", async () => {
    const user = userEvent.setup()
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
    await openDraftTab(user)
    expect(screen.getByText("samplelab-rebilling")).toBeInTheDocument()
    expect(screen.getByText("references/client_rules.md")).toBeInTheDocument()
    expect(screen.queryByText("references/missing.md")).toBeNull()
  })

  it("exposes classification and draft tabs", () => {
    renderSidebar()
    expect(screen.getByRole("tab", { name: "Classification" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Draft" })).toBeInTheDocument()
    expect(screen.queryByRole("tab", { name: /Audit/ })).toBeNull()
    expect(screen.getByText("Audit log")).toBeInTheDocument()
    expect(screen.queryByRole("tab", { name: "Context" })).toBeNull()
    expect(screen.queryByRole("tab", { name: "Insight" })).toBeNull()
    expect(screen.queryByRole("tab", { name: "Timeline" })).toBeNull()
  })

  it("shows Timeline under Insight in the same view when thread context is on", async () => {
    getContextMock.mockResolvedValue({
      version: 1,
      user_notes: "Do not CC legal",
      facts: [
        { id: "fact-1", body: "Asked for Friday", source_message_id: "msg-a", created_at: null },
      ],
      updated_at: "2026-09-04T12:00:00Z",
    })
    const user = userEvent.setup()
    renderSidebar()

    expect(await screen.findByRole("tab", { name: "Context" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Insight" })).toBeInTheDocument()
    expect(screen.queryByRole("tab", { name: "Timeline" })).toBeNull()
    expect(screen.getByRole("tab", { name: "Classification" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Draft" })).toBeInTheDocument()
    expect(screen.queryByRole("tab", { name: /Audit/ })).toBeNull()

    await user.click(screen.getByRole("tab", { name: "Context" }))
    expect(screen.queryByText("Acknowledge and resolve.")).toBeNull()
    expect(screen.queryByText("Timeline")).toBeNull()
    expect(screen.queryByText("Audit log")).toBeNull()

    await user.click(screen.getByRole("tab", { name: "Insight" }))
    expect(screen.getByText("Acknowledge and resolve.")).toBeInTheDocument()
    expect(screen.getByText("Timeline")).toBeInTheDocument()
    expect(screen.queryByText("Audit log")).toBeNull()

    await user.click(screen.getByRole("tab", { name: "Classification" }))
    expect(screen.getByText("Audit log")).toBeInTheDocument()
  })
})

describe("ThreadTriageSidebar internal tag", () => {
  it("shows Internal on same-company triage", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <ThreadTriageSidebar
          threadId="thread-1"
          thread={thread}
          classification={null}
          draft={null}
          triage={{
            is_spam: false,
            has_action_items: true,
            needs_context: false,
            spam_reason: null,
            context_reason: null,
            action_items_summary: "Review policy",
            outcome: "triage.action_needed",
            is_internal: true,
          }}
          auditLog={[]}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByText("Internal")).toBeInTheDocument()
    expect(screen.getByText("Not spam")).toBeInTheDocument()
  })
})

describe("ThreadTriageSidebar not-spam action", () => {
  it("shows Mark as not spam on a SPAM thread", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <ThreadTriageSidebar
          threadId="thread-1"
          thread={{ ...thread, state: "SPAM", last_sender: "orders@sample-lab.example.com" }}
          classification={null}
          draft={null}
          triage={{
            is_spam: true,
            has_action_items: false,
            needs_context: false,
            spam_reason: "Marketing blast",
            context_reason: null,
            action_items_summary: null,
            outcome: "triage.spam_discarded",
            is_internal: false,
          }}
          auditLog={[]}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByRole("button", { name: "Mark as not spam" })).toBeInTheDocument()
  })

  it("hides Mark as not spam when the thread is not spam", () => {
    renderSidebar(null)
    expect(screen.queryByRole("button", { name: "Mark as not spam" })).toBeNull()
  })
})
