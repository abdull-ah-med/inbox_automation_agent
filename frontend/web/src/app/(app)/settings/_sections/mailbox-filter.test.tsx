import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { MailboxFilter } from "@/app/(app)/settings/_sections/mailbox-filter"
import type { MailboxOverview } from "@/lib/types"

const mailbox = (key: string, label: string): MailboxOverview => ({
  mailbox: key,
  email_address: `${key}@example.com`,
  label,
  thread_count: 0,
  unread_count: 0,
  awaiting_action_count: 0,
  filtered_count: 0,
  stale_count: 0,
  urgency_breakdown: {},
  recent_threads: [],
})

describe("MailboxFilter", () => {
  it("shows mailbox labels in the shared Select component", () => {
    render(
      <MailboxFilter
        mailbox="sampleagent"
        mailboxes={[mailbox("sampleagent", "Elise"), mailbox("support", "Support")]}
        onMailboxChange={vi.fn()}
      />,
    )

    expect(screen.getByRole("combobox", { name: "Mailbox" })).toHaveTextContent("Elise")
  })
})
