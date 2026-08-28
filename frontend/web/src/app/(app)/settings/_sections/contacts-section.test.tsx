import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ContactsSection } from "@/app/(app)/settings/_sections/contacts-section"
import type { MailboxContactView } from "@/lib/types"

const listMock = vi.fn()
const upsertMock = vi.fn()
const updateMock = vi.fn()
const removeMock = vi.fn()
const mailboxesMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    mailboxes: {
      list: (...args: unknown[]) => mailboxesMock(...args),
    },
    mailboxContacts: {
      list: (...args: unknown[]) => listMock(...args),
      upsert: (...args: unknown[]) => upsertMock(...args),
      update: (...args: unknown[]) => updateMock(...args),
      remove: (...args: unknown[]) => removeMock(...args),
    },
  },
}))

const contact = (
  overrides: Partial<MailboxContactView> & Pick<MailboxContactView, "email" | "first_name">,
): MailboxContactView => ({
  full_name: "",
  notes: null,
  created_at: "2026-08-01T12:00:00Z",
  updated_at: "2026-08-25T12:00:00Z",
  ...overrides,
})

const renderSection = (client?: QueryClient) => {
  const queryClient =
    client ??
    new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
  return {
    client: queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <ContactsSection />
      </QueryClientProvider>,
    ),
  }
}

describe("ContactsSection", () => {
  beforeEach(() => {
    listMock.mockReset()
    upsertMock.mockReset()
    updateMock.mockReset()
    removeMock.mockReset()
    mailboxesMock.mockReset()
    mailboxesMock.mockResolvedValue([
      {
        mailbox: "sales",
        email_address: "sales@example.com",
        label: "Sales",
        thread_count: 0,
        unread_count: 0,
        awaiting_action_count: 0,
        filtered_count: 0,
        stale_count: 0,
        urgency_breakdown: {},
        recent_threads: [],
      },
    ])
  })

  it("renders visible rows for three contacts", async () => {
    listMock.mockResolvedValue({
      total: 3,
      items: [
        contact({ email: "a@example.com", first_name: "Ada", full_name: "Ada Lovelace" }),
        contact({ email: "b@example.com", first_name: "Bob" }),
        contact({ email: "c@example.com", first_name: "Cara" }),
      ],
    })
    renderSection()
    expect(await screen.findByText("a@example.com")).toBeInTheDocument()
    expect(screen.getByText("Ada")).toBeInTheDocument()
    expect(screen.getByText("b@example.com")).toBeInTheDocument()
    expect(screen.getByText("c@example.com")).toBeInTheDocument()
  })

  it("shows empty state when list is empty", async () => {
    listMock.mockResolvedValue({ total: 0, items: [] })
    renderSection()
    expect(await screen.findByText(/No saved contacts yet/i)).toBeInTheDocument()
  })

  it("filter narrows the visible rows", async () => {
    const user = userEvent.setup()
    listMock.mockImplementation((_mailbox: string, params: { q?: string } = {}) => {
      const all = [
        contact({ email: "kelvin@example.com", first_name: "Kelvin" }),
        contact({ email: "other@example.com", first_name: "Other" }),
      ]
      const q = params.q?.toLowerCase()
      const items = q
        ? all.filter((row) => row.email.includes(q) || row.first_name.toLowerCase().includes(q))
        : all
      return Promise.resolve({ total: items.length, items })
    })
    renderSection()
    expect(await screen.findByText("kelvin@example.com")).toBeInTheDocument()
    expect(screen.getByText("other@example.com")).toBeInTheDocument()
    await user.type(screen.getByLabelText("Filter by name or email"), "kelvin")
    await waitFor(() => {
      expect(screen.queryByText("other@example.com")).not.toBeInTheDocument()
      expect(screen.getByText("kelvin@example.com")).toBeInTheDocument()
    })
  })

  it("adds a contact and shows the new row", async () => {
    const user = userEvent.setup()
    let items: MailboxContactView[] = []
    listMock.mockImplementation(() => Promise.resolve({ total: items.length, items }))
    upsertMock.mockImplementation(
      (_mailbox: string, body: { email: string; first_name: string }) => {
        const row = contact({ email: body.email, first_name: body.first_name })
        items = [...items, row]
        return Promise.resolve(row)
      },
    )
    renderSection()
    expect(await screen.findByText(/No saved contacts yet/i)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Add contact" }))
    const dialog = await screen.findByRole("dialog")
    await user.type(within(dialog).getByLabelText("Contact email"), "new@example.com")
    await user.type(within(dialog).getByLabelText("Contact first name"), "New")
    await user.click(within(dialog).getByRole("button", { name: "Save contact" }))
    expect(await screen.findByText("new@example.com")).toBeInTheDocument()
    expect(screen.getByText("New")).toBeInTheDocument()
  })

  it("edits an existing first name in the table", async () => {
    const user = userEvent.setup()
    let items = [contact({ email: "a@example.com", first_name: "Ada" })]
    listMock.mockImplementation(() => Promise.resolve({ total: items.length, items }))
    updateMock.mockImplementation(
      (_mailbox: string, body: { email: string; first_name: string }) => {
        items = items.map((row) =>
          row.email === body.email ? { ...row, first_name: body.first_name } : row,
        )
        return Promise.resolve(items[0])
      },
    )
    renderSection()
    expect(await screen.findByText("Ada")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Edit contact a@example.com" }))
    const dialog = await screen.findByRole("dialog")
    const firstName = within(dialog).getByLabelText("Contact first name")
    await user.clear(firstName)
    await user.type(firstName, "Adelaide")
    await user.click(within(dialog).getByRole("button", { name: "Save contact" }))
    expect(await screen.findByText("Adelaide")).toBeInTheDocument()
    expect(screen.queryByText("Ada")).not.toBeInTheDocument()
  })

  it("requires confirm before delete and removes the row", async () => {
    const user = userEvent.setup()
    let items = [contact({ email: "a@example.com", first_name: "Ada" })]
    listMock.mockImplementation(() => Promise.resolve({ total: items.length, items }))
    removeMock.mockImplementation(() => {
      items = []
      return Promise.resolve(undefined)
    })
    const { client } = renderSection()
    const invalidateSpy = vi.spyOn(client, "invalidateQueries")
    expect(await screen.findByText("a@example.com")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Delete contact a@example.com" }))
    expect(screen.getByText(/Remove alias for a@example.com/)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Confirm delete contact" }))
    await waitFor(() => {
      expect(screen.queryByText("a@example.com")).not.toBeInTheDocument()
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["mailbox", "sales", "contacts"] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["thread"] })
  })
})
