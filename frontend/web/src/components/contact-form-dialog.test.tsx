import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ComponentProps } from "react"

import { ContactFormDialog } from "@/components/contact-form-dialog"

const getOneMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    mailboxContacts: {
      getOne: (...args: unknown[]) => getOneMock(...args),
      upsert: vi.fn(),
      update: vi.fn(),
    },
    drafts: {
      applySalutation: vi.fn(),
    },
  },
}))

const renderDialog = (props: Partial<ComponentProps<typeof ContactFormDialog>> = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ContactFormDialog
        mode="edit"
        mailbox="elise@example.com"
        open
        onOpenChange={vi.fn()}
        initialEmail="samplecontact@example.com"
        initialFirstName="Kelvin"
        emailLocked
        {...props}
      />
    </QueryClientProvider>,
  )
}

describe("ContactFormDialog", () => {
  beforeEach(() => {
    getOneMock.mockReset()
  })

  it("loads saved full name when reopening an existing contact", async () => {
    getOneMock.mockResolvedValue({
      email: "samplecontact@example.com",
      first_name: "Kelvin",
      full_name: "Kelvin Collado",
      notes: "Goes by Kel",
      created_at: "2026-08-01T12:00:00Z",
      updated_at: "2026-08-25T12:00:00Z",
    })
    renderDialog({ initialFullName: "" })
    await waitFor(() => {
      expect(screen.getByLabelText("Contact full name")).toHaveValue("Kelvin Collado")
    })
    expect(screen.getByLabelText("Contact notes")).toHaveValue("Goes by Kel")
    expect(getOneMock).toHaveBeenCalledWith("elise@example.com", "samplecontact@example.com")
  })

  it("keeps an explicitly passed full name even before fetch settles", async () => {
    getOneMock.mockImplementation(
      () =>
        new Promise(() => {
          /* never resolves */
        }),
    )
    renderDialog({ initialFullName: "Kelvin Collado" })
    expect(await screen.findByLabelText("Contact full name")).toHaveValue("Kelvin Collado")
  })
})
