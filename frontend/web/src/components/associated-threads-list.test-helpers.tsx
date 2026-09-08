import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { vi } from "vitest"

import { AssociatedThreadsList } from "@/components/associated-threads-list"
import type { RelatedThreadItem } from "@/lib/types"

export const reviewMock = vi.fn()
export const detailMock = vi.fn()

export const item: RelatedThreadItem = {
  thread_id: "assoc-1",
  mailbox: "cr@example.com",
  subject: "SampleClient follow-up 8/14",
  sender: "rep@sample-client.example.com",
  last_message_at: "2026-08-10T14:00:00Z",
  urgency: "NORMAL",
  score: 0.8,
  status: "proposed",
}

export const PACKET = "SampleClient packet due Friday the 14th."
export const SOURCE_SUBJECT = "Hart reminder 8/15"

export const renderList = (items: RelatedThreadItem[]) => {
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

export const resetAssociatedThreadsListMocks = () => {
  reviewMock.mockReset()
  reviewMock.mockImplementation(
    async (_src: string, _relatedId: string, body: { status: "confirmed" | "dismissed" }) => ({
      status: body.status,
    }),
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
        reply_text: PACKET,
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
}
