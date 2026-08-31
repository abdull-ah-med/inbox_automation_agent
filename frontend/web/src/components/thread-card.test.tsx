import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { ThreadCard } from "@/components/thread-card"
import type { ThreadSummary } from "@/lib/types"

const thread = (triage: ThreadSummary["triage"]): ThreadSummary => ({
  id: "e25cc63b-9db9-4c2d-af45-e598a639eaec",
  mailbox: "elise@sample-site.example.com",
  mailbox_key: "elise",
  subject: "Q3 compliance checklist",
  state: "DRAFTED",
  urgency: "NORMAL",
  urgency_reason: null,
  category: null,
  last_message_at: "2026-08-18T14:00:00Z",
  last_sender: "kelvin@sample-site.example.com",
  preview: "Please review the attached checklist",
  staleness_hours: 2,
  message_count: 1,
  has_draft: true,
  teaching_note: null,
  triage,
  outlook_url: null,
})

describe("ThreadCard internal tag", () => {
  it("shows Internal and not Spam for same-company mail", () => {
    render(
      <ThreadCard
        thread={thread({
          is_spam: false,
          has_action_items: true,
          needs_context: false,
          spam_reason: null,
          context_reason: null,
          action_items_summary: "Review checklist",
          outcome: "triage.action_needed",
          is_internal: true,
        })}
      />,
    )
    expect(screen.getByText("Internal")).toBeInTheDocument()
    expect(screen.queryByText("Spam")).toBeNull()
  })

  it("does not show Internal on external mail", () => {
    render(
      <ThreadCard
        thread={thread({
          is_spam: true,
          has_action_items: false,
          needs_context: false,
          spam_reason: "Marketing blast",
          context_reason: null,
          action_items_summary: null,
          outcome: "triage.spam_discarded",
          is_internal: false,
        })}
      />,
    )
    expect(screen.queryByText("Internal")).toBeNull()
    expect(screen.getByText("Spam")).toHaveClass("text-[#F97316]")
  })

  it("shows Automated on noreply mail", () => {
    render(
      <ThreadCard
        thread={thread({
          is_spam: false,
          has_action_items: false,
          needs_context: false,
          spam_reason: null,
          context_reason: null,
          action_items_summary: null,
          outcome: "triage.no_action_discarded",
          is_internal: false,
          is_automated: true,
        })}
      />,
    )
    expect(screen.getByText("Automated")).toBeInTheDocument()
  })

  it("shows a Not spam action on filtered spam threads", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <ThreadCard
          thread={{
            ...thread({
              is_spam: true,
              has_action_items: false,
              needs_context: false,
              spam_reason: "Marketing blast",
              context_reason: null,
              action_items_summary: null,
              outcome: "triage.spam_discarded",
              is_internal: false,
            }),
            state: "SPAM",
          }}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByRole("button", { name: "Mark as not spam" })).toBeInTheDocument()
  })

  it("does not show a Not spam action on drafted threads", () => {
    render(
      <ThreadCard
        thread={thread({
          is_spam: false,
          has_action_items: true,
          needs_context: false,
          spam_reason: null,
          context_reason: null,
          action_items_summary: "Review checklist",
          outcome: "triage.action_needed",
          is_internal: false,
        })}
      />,
    )
    expect(screen.queryByRole("button", { name: "Mark as not spam" })).toBeNull()
  })
})
