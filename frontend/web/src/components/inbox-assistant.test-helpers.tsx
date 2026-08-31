import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { vi } from "vitest"
import type { ReactElement } from "react"

export const askChat = vi.fn()
export const listMailboxes = vi.fn()
export const createSession = vi.fn()
export const getSession = vi.fn()

export const THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

export const mailboxList = [
  {
    mailbox: "sales",
    email_address: "sales@example.com",
    label: "Sales",
    thread_count: 4,
    unread_count: 0,
    awaiting_action_count: 1,
    filtered_count: 0,
    stale_count: 0,
    urgency_breakdown: {},
    recent_threads: [],
  },
]

export const groundedResponse = {
  answer: "The overdue billing dispute is waiting on review.",
  citations: [
    {
      thread_id: THREAD_ID,
      mailbox: "sales@example.com",
      subject: "Invoice dispute — overdue billing",
      state: "REQUIRES_HUMAN",
      urgency: "HIGH",
      snippet: "Please review the overdue billing packet.",
      url_path: `/threads/${THREAD_ID}`,
    },
  ],
  retrieval_count: 1,
  mailbox: null,
  refused_write: false,
}

export type GroundedVerifier = "SUPPORTED" | "UNSUPPORTED" | "SKIPPED" | "UNKNOWN"

export const deliverStream = (
  handlers: {
    onMeta?: (meta: {
      citations: typeof groundedResponse.citations
      retrieval_count: number
      mailbox: string | null
      refused_write: boolean
      cached?: boolean
      grounded_verifier?: GroundedVerifier
    }) => void
    onDelta?: (text: string) => void
    onDone?: (groundedVerifier?: GroundedVerifier) => void
    onStatus?: (text: string) => void
  },
  answer = groundedResponse.answer,
  extra: Partial<typeof groundedResponse> & {
    cached?: boolean
    grounded_verifier?: GroundedVerifier
  } = {},
) => {
  // H1: the backend now sends the eager meta with a placeholder verdict and
  // carries the real, post-verification verdict on the terminal "done" event
  // instead of a second "meta" — mirror that wire shape here.
  handlers.onMeta?.({
    citations: extra.citations ?? groundedResponse.citations,
    retrieval_count: extra.retrieval_count ?? 1,
    mailbox: extra.mailbox ?? null,
    refused_write: extra.refused_write ?? false,
    cached: extra.cached,
    grounded_verifier: "SKIPPED",
  })
  handlers.onDelta?.(answer)
  handlers.onDone?.(extra.grounded_verifier)
}

export const renderWithClient = (ui: ReactElement) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

export const resetInboxAssistantMocks = () => {
  askChat.mockReset()
  listMailboxes.mockReset()
  createSession.mockReset()
  getSession.mockReset()
  window.localStorage.clear()
  listMailboxes.mockResolvedValue(mailboxList)
  createSession.mockResolvedValue({
    session_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    mailbox: null,
  })
  getSession.mockRejectedValue(new Error("missing"))
  askChat.mockImplementation(
    async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
      deliverStream(handlers)
    },
  )
}
