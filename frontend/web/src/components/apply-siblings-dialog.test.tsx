import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ApplySiblingsDialog } from "@/components/apply-siblings-dialog"
import type { RelatedThreadItem } from "@/lib/types"

const applyTreatmentMock = vi.fn()
const reviewRelatedMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      applyTreatment: (...args: unknown[]) => applyTreatmentMock(...args),
      reviewRelated: (...args: unknown[]) => reviewRelatedMock(...args),
    },
  },
}))

const sampleclient: RelatedThreadItem = {
  thread_id: "sib-sampleclient",
  mailbox: "sales@example.com",
  subject: "SampleClient follow-up 8/14",
  sender: "rep@sample-client.example.com",
  last_message_at: "2026-08-14T15:00:00Z",
  urgency: "NORMAL",
  score: 0.02,
  status: "proposed",
}

const invoice: RelatedThreadItem = {
  thread_id: "sib-invoice",
  mailbox: "sales@example.com",
  subject: "January invoice",
  sender: "rep@sample-client.example.com",
  last_message_at: "2026-08-10T14:00:00Z",
  urgency: "LOW",
  score: 0.01,
  status: "proposed",
}

const renderDialog = (
  items: RelatedThreadItem[],
  treatment: "no_reply" | "urgency" = "no_reply",
) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const onOpenChange = vi.fn()
  return {
    onOpenChange,
    ...render(
      <QueryClientProvider client={client}>
        <ApplySiblingsDialog
          open={items.length > 0}
          sourceThreadId="thread-src"
          items={items}
          treatment={treatment}
          reason="Same SampleClient drip"
          urgency={treatment === "urgency" ? "HIGH" : undefined}
          onOpenChange={onOpenChange}
        />
      </QueryClientProvider>,
    ),
  }
}

describe("ApplySiblingsDialog", () => {
  beforeEach(() => {
    applyTreatmentMock.mockReset()
    reviewRelatedMock.mockReset()
    applyTreatmentMock.mockResolvedValue({ applied_thread_ids: [sampleclient.thread_id] })
    reviewRelatedMock.mockResolvedValue({ status: "dismissed" })
  })

  it("does not render a dialog when there are no siblings", () => {
    renderDialog([])
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("names the no-reply action and lists mailbox, subject, US date, and urgency", async () => {
    renderDialog([sampleclient, invoice])
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("Apply no reply")
    expect(dialog).not.toHaveTextContent("Are you sure?")
    expect(dialog).toHaveTextContent("SampleClient follow-up 8/14")
    expect(dialog).toHaveTextContent("sales@example.com")
    expect(dialog).toHaveTextContent("NORMAL")
    expect(dialog).toHaveTextContent("8/14/2026")
    expect(dialog).not.toHaveTextContent("14/8/2026")
    expect(within(dialog).getByRole("button", { name: "Apply to selected" })).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Skip" })).toBeInTheDocument()
  })

  it("applies no reply to the checked subset only", async () => {
    const user = userEvent.setup()
    renderDialog([sampleclient, invoice])
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("checkbox", { name: /January invoice/ }))
    await user.click(within(dialog).getByRole("button", { name: "Apply to selected" }))
    await waitFor(() => {
      expect(applyTreatmentMock).toHaveBeenCalledWith("thread-src", {
        treatment: "no_reply",
        thread_ids: ["sib-sampleclient"],
        reason: "Same SampleClient drip",
      })
    })
    expect(reviewRelatedMock).toHaveBeenCalledWith("thread-src", "sib-invoice", {
      status: "dismissed",
    })
  })

  it("skip dismisses every proposed sibling and does not apply treatment", async () => {
    const user = userEvent.setup()
    renderDialog([sampleclient])
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: "Skip" }))
    await waitFor(() => {
      expect(reviewRelatedMock).toHaveBeenCalledWith("thread-src", "sib-sampleclient", {
        status: "dismissed",
      })
    })
    expect(applyTreatmentMock).not.toHaveBeenCalled()
  })
})
