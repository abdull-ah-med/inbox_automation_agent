import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { TeachingNotesSection } from "@/app/(app)/settings/_sections/teaching-notes-section"

const listMock = vi.fn()
const removeMock = vi.fn()
const mailboxesMock = vi.fn()

vi.mock("@/features/auth/use-auth", () => ({
  useAuthState: () => ({
    user: { email: "admin@example.com", role: "admin" },
  }),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    mailboxes: { list: (...args: unknown[]) => mailboxesMock(...args) },
    teachingNotes: {
      list: (...args: unknown[]) => listMock(...args),
      remove: (...args: unknown[]) => removeMock(...args),
    },
  },
}))

const mailbox = {
  mailbox: "sales@example.com",
  email_address: "sales@example.com",
  label: "Sales",
  thread_count: 0,
  unread_count: 0,
  awaiting_action_count: 0,
  filtered_count: 0,
  stale_count: 0,
  urgency_breakdown: {},
  recent_threads: [],
}

const renderSection = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <TeachingNotesSection mailbox="sales@example.com" mailboxesLoading={false} />
    </QueryClientProvider>,
  )
}

describe("TeachingNotesSection", () => {
  beforeEach(() => {
    listMock.mockReset()
    removeMock.mockReset()
    mailboxesMock.mockReset()
    mailboxesMock.mockResolvedValue([mailbox])
  })

  it("renders empty state for the selected mailbox", async () => {
    listMock.mockResolvedValue([])
    renderSection()
    expect(await screen.findByText("Teaching notes")).toBeInTheDocument()
    expect(await screen.findByText("No teaching notes yet for this mailbox.")).toBeInTheDocument()
  })

  it("archives a note by id", async () => {
    const user = userEvent.setup()
    listMock.mockResolvedValue([
      {
        id: "note-1",
        mailbox: "sales@example.com",
        title: "Ack driver",
        body: "Always acknowledge the driver by name",
        applies_when: null,
        scope: "mailbox",
        scope_key: "mailbox:sales@example.com",
        status: "active",
        origin: "manual",
        origin_atom_id: null,
        person_bound: false,
        hit_count: 0,
        precision_num: 0,
        precision_den: 0,
        created_at: null,
        updated_at: null,
      },
    ])
    removeMock.mockResolvedValue(undefined)
    renderSection()
    await user.click(
      await screen.findByRole("button", { name: "Archive teaching note Ack driver" }),
    )
    await waitFor(() => {
      expect(removeMock).toHaveBeenCalledWith("note-1")
    })
  })
})
