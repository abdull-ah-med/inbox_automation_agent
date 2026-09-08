import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { AssociatedThreadsList } from "@/components/associated-threads-list"
import {
  detailMock,
  item,
  PACKET,
  renderList,
  resetAssociatedThreadsListMocks,
  reviewMock,
  SOURCE_SUBJECT,
} from "@/components/associated-threads-list.test-helpers"
import type { RelatedThreadItem } from "@/lib/types"

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      reviewRelated: (...args: unknown[]) => reviewMock(...args),
      detail: (...args: unknown[]) => detailMock(...args),
    },
  },
}))

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}))

describe("AssociatedThreadsList", () => {
  beforeEach(resetAssociatedThreadsListMocks)

  it("renders nothing when there are no associated threads", () => {
    const { container } = renderList([])
    expect(container).toBeEmptyDOMElement()
  })

  it("lists mailbox, US date, confirm, and remove", () => {
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
    ).toHaveTextContent("Confirm")
    expect(
      screen.getByRole("button", { name: "Remove associated thread SampleClient follow-up 8/14" }),
    ).toHaveTextContent("Remove")
  })

  it("shows per-row confirm when multiple proposed associations are listed", () => {
    const second: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-2",
      subject: "Invoice packet 8/12",
    }
    renderList([item, second])
    expect(
      screen.getByRole("button", { name: "Confirm associated thread SampleClient follow-up 8/14" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Confirm associated thread Invoice packet 8/12" }),
    ).toBeInTheDocument()
  })

  it("confirmed row hides confirm and keeps remove control when alone", () => {
    renderList([{ ...item, status: "confirmed" }])
    expect(
      screen.queryByRole("button", {
        name: "Confirm associated thread SampleClient follow-up 8/14",
      }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument()
    expect(screen.getByText(/confirmed/)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Delete association SampleClient follow-up 8/14" }),
    ).toHaveTextContent("Delete Association")
  })

  it("hides confirmed row actions when multiple associations are listed", () => {
    const confirmed: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-confirmed",
      subject: "Confirmed packet 8/10",
      status: "confirmed",
    }
    const proposed: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-proposed",
      subject: "Invoice packet 8/12",
      status: "proposed",
    }
    renderList([confirmed, proposed])
    expect(
      screen.queryByRole("button", { name: "Delete association Confirmed packet 8/10" }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Remove associated thread Invoice packet 8/12" }),
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

  it("shows dismissed associations again after switching source threads", async () => {
    const user = userEvent.setup()
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <AssociatedThreadsList
          sourceThreadId="thread-src"
          sourceSubject={SOURCE_SUBJECT}
          items={[item]}
        />
      </QueryClientProvider>,
    )
    await user.click(
      screen.getByRole("button", {
        name: "Remove associated thread SampleClient follow-up 8/14",
      }),
    )
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Associated threads" })).not.toBeInTheDocument()
    })

    rerender(
      <QueryClientProvider client={client}>
        <AssociatedThreadsList
          sourceThreadId="thread-other"
          sourceSubject={SOURCE_SUBJECT}
          items={[item]}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByRole("heading", { name: "Associated threads" })).toBeInTheDocument()
    expect(screen.getByText("SampleClient follow-up 8/14")).toBeInTheDocument()
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
        name: "Delete association SampleClient follow-up 8/14",
      }),
    )
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Associated threads" })).not.toBeInTheDocument()
    })
  })

  it("lets two rows review in parallel and shows the latest server status", async () => {
    const user = userEvent.setup()
    let releaseA: (value: { status: "confirmed" | "dismissed" }) => void = () => {}
    const aPromise = new Promise<{ status: "confirmed" | "dismissed" }>((resolve) => {
      releaseA = resolve
    })
    reviewMock.mockImplementation(
      async (_src: string, relatedId: string, body: { status: "confirmed" | "dismissed" }) => {
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
      screen.getByRole("checkbox", {
        name: "Select associated thread SampleClient follow-up 8/14",
      }),
    )
    await user.click(screen.getByRole("button", { name: "Accept" }))
    await user.click(
      screen.getByRole("button", {
        name: "Remove associated thread Invoice packet 8/12",
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

  it("disables bulk actions when both confirmed and proposed rows are selected", async () => {
    const user = userEvent.setup()
    const confirmed: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-confirmed",
      subject: "Confirmed packet 8/10",
      status: "confirmed",
    }
    const proposed: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-proposed",
      subject: "Invoice packet 8/12",
      status: "proposed",
    }
    renderList([confirmed, proposed])

    await user.click(
      screen.getByRole("checkbox", { name: "Select associated thread Confirmed packet 8/10" }),
    )
    await user.click(
      screen.getByRole("checkbox", { name: "Select associated thread Invoice packet 8/12" }),
    )

    expect(screen.getByRole("button", { name: "Accept all" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reject all" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Delete all associations" })).toBeDisabled()
    expect(reviewMock).not.toHaveBeenCalled()
  })

  it("bulk deletes only selected confirmed associations", async () => {
    const user = userEvent.setup()
    const confirmed: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-confirmed",
      subject: "Confirmed packet 8/10",
      status: "confirmed",
    }
    const proposed: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-proposed",
      subject: "Invoice packet 8/12",
      status: "proposed",
    }
    renderList([confirmed, proposed])

    await user.click(
      screen.getByRole("checkbox", { name: "Select associated thread Confirmed packet 8/10" }),
    )
    await user.click(screen.getByRole("button", { name: "Delete all associations" }))
    await waitFor(() => {
      expect(screen.queryByText("Confirmed packet 8/10")).not.toBeInTheDocument()
    })
    expect(screen.getByText("Invoice packet 8/12")).toBeInTheDocument()
    expect(reviewMock).toHaveBeenCalledWith(
      "thread-src",
      "assoc-confirmed",
      { status: "dismissed" },
      expect.any(AbortSignal),
    )
    expect(reviewMock).not.toHaveBeenCalledWith(
      "thread-src",
      "assoc-proposed",
      { status: "dismissed" },
      expect.any(AbortSignal),
    )
  })

  it("bulk accepts and rejects only selected proposed associations", async () => {
    const user = userEvent.setup()
    const first: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-1",
      subject: "SampleClient follow-up 8/14",
      status: "proposed",
    }
    const second: RelatedThreadItem = {
      ...item,
      thread_id: "assoc-2",
      subject: "Invoice packet 8/12",
      status: "proposed",
    }
    renderList([first, second])

    await user.click(
      screen.getByRole("checkbox", { name: "Select associated thread SampleClient follow-up 8/14" }),
    )
    await user.click(screen.getByRole("button", { name: "Accept" }))
    await waitFor(() => {
      expect(screen.getByText(/confirmed/)).toBeInTheDocument()
    })
    expect(reviewMock).toHaveBeenCalledWith(
      "thread-src",
      "assoc-1",
      { status: "confirmed" },
      expect.any(AbortSignal),
    )

    await user.click(
      screen.getByRole("checkbox", { name: "Select associated thread Invoice packet 8/12" }),
    )
    await user.click(screen.getByRole("button", { name: "Reject all" }))
    await waitFor(() => {
      expect(screen.queryByText("Invoice packet 8/12")).not.toBeInTheDocument()
    })
    expect(reviewMock).toHaveBeenCalledWith(
      "thread-src",
      "assoc-2",
      { status: "dismissed" },
      expect.any(AbortSignal),
    )
  })

  it("shows glanceable match reason chips", () => {
    renderList([
      {
        ...item,
        match_reasons: ["same_sender", "near_subject", "shared_deadline"],
      },
    ])
    expect(screen.getByText("Same sender")).toBeInTheDocument()
    expect(screen.getByText("Similar subject")).toBeInTheDocument()
    expect(screen.getByText("Shared deadline")).toBeInTheDocument()
  })

  it("labels exact subject matches as same subject", () => {
    renderList([
      {
        ...item,
        match_reasons: ["same_sender", "same_subject"],
      },
    ])
    expect(screen.getByText("Same subject")).toBeInTheDocument()
    expect(screen.queryByText("Similar subject")).not.toBeInTheDocument()
  })

  it("replaces the list when items prop changes", () => {
    const { rerender } = renderList([item])
    expect(screen.getByText("SampleClient follow-up 8/14")).toBeInTheDocument()
    rerender(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
          })
        }
      >
        <AssociatedThreadsList
          sourceThreadId="thread-src"
          sourceSubject={SOURCE_SUBJECT}
          items={[
            {
              ...item,
              thread_id: "assoc-2",
              subject: "Vendor invoice 9/1",
            },
          ]}
        />
      </QueryClientProvider>,
    )
    expect(screen.queryByText("SampleClient follow-up 8/14")).not.toBeInTheDocument()
    expect(screen.getByText("Vendor invoice 9/1")).toBeInTheDocument()
  })
})
