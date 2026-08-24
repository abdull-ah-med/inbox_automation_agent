import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { AssociatedThreadsList } from "@/components/associated-threads-list"
import type { RelatedThreadItem } from "@/lib/types"

const reviewMock = vi.fn()
const detailMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      reviewRelated: (...args: unknown[]) => reviewMock(...args),
      detail: (...args: unknown[]) => detailMock(...args),
    },
  },
}))

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string
    children: React.ReactNode
  }) => <a href={href}>{children}</a>,
}))

const item: RelatedThreadItem = {
  thread_id: "assoc-1",
  mailbox: "cr@example.com",
  subject: "SampleClient follow-up 8/14",
  sender: "rep@sample-client.example.com",
  last_message_at: "2026-08-10T14:00:00Z",
  urgency: "NORMAL",
  score: 0.8,
  status: "proposed",
}

const PACKET = "SampleClient packet due Friday the 14th."
const SOURCE_SUBJECT = "Hart reminder 8/15"

const renderList = (items: RelatedThreadItem[]) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <AssociatedThreadsList
        sourceThreadId="thread-src"
        sourceSubject={SOURCE_SUBJECT}
        items={items}
      />
    </QueryClientProvider>,
  )
}

describe("AssociatedThreadsList", () => {
  beforeEach(() => {
    reviewMock.mockReset()
    reviewMock.mockImplementation(
      async (
        _src: string,
        _relatedId: string,
        body: { status: "confirmed" | "dismissed" },
      ) => ({ status: body.status }),
    )
    detailMock.mockReset()
    detailMock.mockResolvedValue({
      thread: {
        id: "assoc-1",
        mailbox: "cr@example.com",
        mailbox_key: "cr",
        subject: "SampleClient follow-up 8/14",
        state: "DRAFTED",
        urgency: "NORMAL",
        last_message_at: "2026-08-10T14:00:00Z",
        last_sender: "rep@sample-client.example.com",
        message_count: 1,
        outlook_url: null,
      },
      messages: [
        {
          id: "msg-1",
          direction: "inbound",
          sender: "rep@sample-client.example.com",
          to: ["cr@example.com"],
          cc: [],
          bcc: [],
          body_text: PACKET,
          body_preview: "SampleClient packet",
          received_at: "2026-08-10T14:00:00Z",
          has_attachments: false,
          outlook_url: null,
        },
      ],
      classification: null,
      draft: null,
      triage: null,
      audit_log: [],
    })
  })

  it("renders nothing when there are no associated threads", () => {
    const { container } = renderList([])
    expect(container).toBeEmptyDOMElement()
  })

  it("lists mailbox, US date, confirm, and dismiss", () => {
    renderList([item])
    expect(screen.getByText("SampleClient follow-up 8/14")).toBeInTheDocument()
    expect(screen.getByText("cr@example.com")).toBeInTheDocument()
    expect(screen.getByText(/8\/10\/2026/)).toBeInTheDocument()
    expect(
      screen.getByRole("button", {
        name: "Preview associated thread SampleClient follow-up 8/14",
      }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: /SampleClient follow-up 8\/14/ }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Confirm associated thread SampleClient follow-up 8/14" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Dismiss associated thread SampleClient follow-up 8/14" }),
    ).toBeInTheDocument()
  })

  it("opens a preview modal and keeps the source thread on screen", async () => {
    const user = userEvent.setup()
    renderList([item])
    await user.click(
      screen.getByRole("button", {
        name: "Preview associated thread SampleClient follow-up 8/14",
      }),
    )
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("SampleClient follow-up 8/14")
    expect(dialog).toHaveTextContent(SOURCE_SUBJECT)
    expect(dialog).toHaveTextContent(PACKET)
    expect(
      screen.getByRole("region", { name: "Associated threads", hidden: true }),
    ).toBeInTheDocument()
    expect(within(dialog).getByRole("link", { name: "Open full thread" })).toHaveAttribute(
      "href",
      "/threads/assoc-1?from=thread-src",
    )
  })

  it("closes the preview back to the current thread", async () => {
    const user = userEvent.setup()
    renderList([item])
    await user.click(
      screen.getByRole("button", {
        name: "Preview associated thread SampleClient follow-up 8/14",
      }),
    )
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: "Back to current thread" }))
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    })
    expect(screen.getByRole("heading", { name: "Associated threads" })).toBeInTheDocument()
    expect(screen.queryByText(SOURCE_SUBJECT)).not.toBeInTheDocument()
  })

  it("confirm persists confirmed and dismiss persists dismissed", async () => {
    const user = userEvent.setup()
    renderList([item])
    await user.click(
      screen.getByRole("button", {
        name: "Confirm associated thread SampleClient follow-up 8/14",
      }),
    )
    await waitFor(() => {
      expect(
        screen.queryByRole("button", {
          name: "Confirm associated thread SampleClient follow-up 8/14",
        }),
      ).not.toBeInTheDocument()
    })
    expect(screen.getByText(/confirmed/)).toBeInTheDocument()
    await user.click(
      screen.getByRole("button", {
        name: "Dismiss associated thread SampleClient follow-up 8/14",
      }),
    )
    await waitFor(() => {
      expect(
        screen.queryByRole("region", { name: "Associated threads" }),
      ).not.toBeInTheDocument()
    })
  })

  it("lets two rows review in parallel and shows the latest server status", async () => {
    const user = userEvent.setup()
    let releaseA: (value: { status: "confirmed" | "dismissed" }) => void = () => {}
    const aPromise = new Promise<{ status: "confirmed" | "dismissed" }>((resolve) => {
      releaseA = resolve
    })
    reviewMock.mockImplementation(
      async (
        _src: string,
        relatedId: string,
        body: { status: "confirmed" | "dismissed" },
      ) => {
        if (relatedId === "assoc-1") {
          return aPromise
        }
        return { status: body.status }
      },
    )
    const second: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-2",
      subject: "Invoice packet 8/12",
    }
    renderList([item, second])
    await user.click(
      screen.getByRole("button", {
        name: "Confirm associated thread SampleClient follow-up 8/14",
      }),
    )
    await user.click(
      screen.getByRole("button", {
        name: "Dismiss associated thread Invoice packet 8/12",
      }),
    )
    await waitFor(() => {
      expect(screen.queryByText("Invoice packet 8/12")).not.toBeInTheDocument()
    })
    expect(screen.getByText("SampleClient follow-up 8/14")).toBeInTheDocument()
    expect(
      screen.getByRole("button", {
        name: "Confirm associated thread SampleClient follow-up 8/14",
      }),
    ).toBeDisabled()
    await act(async () => {
      releaseA({ status: "confirmed" })
    })
    await waitFor(() => {
      expect(screen.getByText(/confirmed/)).toBeInTheDocument()
    })
    expect(
      screen.queryByRole("button", {
        name: "Confirm associated thread SampleClient follow-up 8/14",
      }),
    ).not.toBeInTheDocument()
    expect(screen.getByText("SampleClient follow-up 8/14")).toBeInTheDocument()
  })
})
