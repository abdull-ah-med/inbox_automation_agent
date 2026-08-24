import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

const threadDetail = vi.fn()
const relatedThreads = vi.fn()
const searchParamsString = vi.fn(
  () => "from=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
)

vi.mock("next/navigation", () => ({
  useParams: () => ({ thread_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" }),
  useSearchParams: () => new URLSearchParams(searchParamsString()),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      detail: (...args: unknown[]) => threadDetail(...args),
      related: (...args: unknown[]) => relatedThreads(...args),
    },
  },
}))

import ThreadDetailPage from "@/app/(app)/threads/[thread_id]/page"

const THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
const ORIGIN_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

const baseThread = {
  id: THREAD_ID,
  mailbox: "sales@example.com",
  mailbox_key: "sales",
  subject: "Invoice dispute — overdue billing",
  state: "REQUIRES_HUMAN",
  urgency: "HIGH",
  urgency_reason: null,
  category: null,
  last_message_at: "2026-08-01T00:00:00Z",
  last_sender: "vendor@example.com",
  preview: null,
  staleness_hours: 4,
  message_count: 2,
  has_draft: false,
  teaching_note: null,
  triage: null,
  outlook_url: null,
}

const originThread = {
  ...baseThread,
  id: ORIGIN_ID,
  subject: "Packet follow-up — needs signature",
}

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ThreadDetailPage />
    </QueryClientProvider>,
  )
}

describe("Thread detail page — useSearchParams Suspense boundary", () => {
  beforeEach(() => {
    threadDetail.mockReset()
    relatedThreads.mockReset()
    relatedThreads.mockResolvedValue({ items: [] })
    searchParamsString.mockReturnValue(
      "from=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
  })

  it("renders the origin banner derived from the ?from= query param without bailing out", async () => {
    threadDetail.mockImplementation(async (id: string) =>
      id === ORIGIN_ID
        ? {
            thread: originThread,
            messages: [],
            classification: null,
            draft: null,
            triage: null,
            audit_log: [],
          }
        : {
            thread: baseThread,
            messages: [],
            classification: null,
            draft: null,
            triage: null,
            audit_log: [],
          },
    )

    renderPage()

    expect(
      await screen.findByRole("link", {
        name: /back to packet follow-up — needs signature/i,
      }),
    ).toHaveAttribute("href", `/threads/${ORIGIN_ID}`)
  })

  it("renders the thread subject when there is no ?from= origin", async () => {
    searchParamsString.mockReturnValue("")
    threadDetail.mockResolvedValue({
      thread: baseThread,
      messages: [],
      classification: null,
      draft: null,
      triage: null,
      audit_log: [],
    })

    renderPage()

    expect(
      await screen.findByRole("heading", {
        name: "Invoice dispute — overdue billing",
      }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/back to/i)).not.toBeInTheDocument()
  })
})
