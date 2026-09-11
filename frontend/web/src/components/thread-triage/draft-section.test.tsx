import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { DraftSection } from "@/components/thread-triage/draft-section"
import type { DraftView, ReplyAddresseeView } from "@/lib/types"

const applySalutationMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    drafts: {
      applySalutation: (...args: unknown[]) => applySalutationMock(...args),
    },
  },
}))

const draft: DraftView = {
  id: "draft-1",
  subject: "Re: Quote",
  body: "Hi Samplecontact,\n\nThanks for reaching out.",
  forward_to: null,
  teaching_note: "",
  urgency: "NORMAL",
  urgency_reason: null,
  created_at: new Date().toISOString(),
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
  correct_actions: [],
}

const renderSection = (replyAddressee: ReplyAddresseeView | null) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <DraftSection
          draft={draft}
          badge={null}
          actionError={null}
          feedbackDone={false}
          busy={false}
          mailboxKey="sales"
          replyAddressee={replyAddressee}
          onApprove={() => undefined}
          onApproveKeyDown={() => undefined}
          onReject={() => undefined}
          onRejectKeyDown={() => undefined}
        />
      </QueryClientProvider>,
    ),
  }
}

describe("DraftSection salute chip", () => {
  beforeEach(() => {
    applySalutationMock.mockReset()
    applySalutationMock.mockResolvedValue({
      draft: {
        ...draft,
        body: "Hi Kelvin,\n\nThanks for reaching out.",
        edited_body: "Hi Kelvin,\n\nThanks for reaching out.",
      },
      reply_addressee: {
        email: "samplecontact@sample-vendor.example.com",
        salute_name: "Kelvin",
        source: "directory",
        source_kind: "directory",
        directory_hit: true,
      },
      contact: {
        email: "samplecontact@sample-vendor.example.com",
        full_name: "Kelvin Collado",
        first_name: "Kelvin",
        notes: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    })
  })

  it("renders the current salute and opens the dialog on click", async () => {
    const user = userEvent.setup()
    renderSection({
      email: "samplecontact@sample-vendor.example.com",
      salute_name: "Kelvin",
      source: "latest_inbound",
      source_kind: "signature",
      directory_hit: false,
    })
    expect(screen.getByRole("button", { name: "Salute: Kelvin" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Salute: Kelvin" }))
    expect(await screen.findByRole("dialog")).toBeInTheDocument()
    expect(screen.getByLabelText("Contact email")).toHaveValue("samplecontact@sample-vendor.example.com")
  })

  it("saves via draft salutation endpoint and invalidates the thread", async () => {
    const user = userEvent.setup()
    const { client } = renderSection({
      email: "samplecontact@sample-vendor.example.com",
      salute_name: "Kelvin",
      source: "latest_inbound",
      source_kind: "signature",
      directory_hit: false,
    })
    const invalidateSpy = vi.spyOn(client, "invalidateQueries")
    await user.click(screen.getByRole("button", { name: "Salute: Kelvin" }))
    const dialog = await screen.findByRole("dialog")
    const firstName = within(dialog).getByLabelText("Contact first name")
    await user.clear(firstName)
    await user.type(firstName, "Kelvin")
    await user.click(within(dialog).getByRole("button", { name: "Save contact" }))
    await waitFor(() => {
      expect(applySalutationMock).toHaveBeenCalledWith("draft-1", {
        email: "samplecontact@sample-vendor.example.com",
        first_name: "Kelvin",
        full_name: "",
        notes: null,
      })
    })
    expect(screen.queryByText(/Regenerate the draft/i)).not.toBeInTheDocument()
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread"] })
  })

  it("renders empty salute as — none — and opens dialog on click", async () => {
    const user = userEvent.setup()
    renderSection({
      email: "samplecontact@sample-vendor.example.com",
      salute_name: "",
      source: "latest_inbound",
      source_kind: "none",
      directory_hit: false,
    })
    expect(screen.getByRole("button", { name: "Salute: — none —" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Salute: — none —" }))
    expect(await screen.findByRole("dialog")).toBeInTheDocument()
  })
})

describe("DraftSection briefing (no email reply)", () => {
  const briefingDraft = { ...draft, body: "", subject: "Invitation: IDME's Demo - 2nd Week" }

  const renderBriefing = (overrides?: Partial<React.ComponentProps<typeof DraftSection>>) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    return render(
      <QueryClientProvider client={client}>
        <DraftSection
          draft={briefingDraft}
          badge={null}
          actionError={null}
          feedbackDone={false}
          busy={false}
          mailboxKey="sales"
          replyAddressee={{
            email: "siddiq@sample-partner.example.com",
            salute_name: "Abu",
            source: "latest_inbound",
            source_kind: "display",
            directory_hit: false,
          }}
          onApprove={() => undefined}
          onApproveKeyDown={() => undefined}
          onReject={() => undefined}
          onRejectKeyDown={() => undefined}
          {...overrides}
        />
      </QueryClientProvider>,
    )
  }

  it("hides Approve and salute and shows that no email reply is needed", () => {
    renderBriefing()
    expect(screen.getByText("No email reply needed")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Approve draft" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Salute:/ })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reject draft" })).toBeInTheDocument()
  })

  it("shows Generate draft on briefing threads when handler is provided", () => {
    renderBriefing({ onGenerateDraft: () => undefined })
    expect(screen.getByRole("button", { name: "Generate draft" })).toBeInTheDocument()
  })

  it("calls generate handler and disables while pending", async () => {
    const user = userEvent.setup()
    const onGenerateDraft = vi.fn()
    const { rerender } = renderBriefing({ onGenerateDraft })
    await user.click(screen.getByRole("button", { name: "Generate draft" }))
    expect(onGenerateDraft).toHaveBeenCalledTimes(1)

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    rerender(
      <QueryClientProvider client={client}>
        <DraftSection
          draft={briefingDraft}
          badge={null}
          actionError={null}
          feedbackDone={false}
          busy={false}
          generatePending
          mailboxKey="sales"
          replyAddressee={null}
          onApprove={() => undefined}
          onApproveKeyDown={() => undefined}
          onReject={() => undefined}
          onRejectKeyDown={() => undefined}
          onGenerateDraft={onGenerateDraft}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByRole("button", { name: "Generate draft" })).toBeDisabled()
    expect(screen.getByText("Generating…")).toBeInTheDocument()
  })
})

describe("DraftSection regenerating", () => {
  it("hides the old letter and shows Regenerating draft", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <DraftSection
          draft={draft}
          badge={null}
          actionError={null}
          feedbackDone={false}
          busy={false}
          generatePending
          mailboxKey="sales"
          replyAddressee={null}
          onApprove={() => undefined}
          onApproveKeyDown={() => undefined}
          onReject={() => undefined}
          onRejectKeyDown={() => undefined}
        />
      </QueryClientProvider>,
    )
    expect(screen.queryByText("Thanks for reaching out.")).not.toBeInTheDocument()
    expect(screen.getByText("Regenerating draft…")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Approve draft" })).not.toBeInTheDocument()
  })
})
