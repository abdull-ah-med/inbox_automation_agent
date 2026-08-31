import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { act, type ReactNode } from "react"
import { describe, expect, it, vi } from "vitest"

import { useSiblingsPrompt } from "@/hooks/use-siblings-prompt"

const relatedMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      related: (...args: unknown[]) => relatedMock(...args),
    },
  },
}))

const THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
const SIBLING_SUBJECT = "Packet follow-up — needs signature"

describe("useSiblingsPrompt", () => {
  it("opens with sibling subjects from the related query", async () => {
    relatedMock.mockResolvedValue({
      items: [
        {
          thread_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
          mailbox: "sales@example.com",
          subject: SIBLING_SUBJECT,
          state: "REQUIRES_HUMAN",
          similarity: 0.91,
          last_message_at: "2026-03-01T12:00:00Z",
        },
      ],
    })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useSiblingsPrompt(THREAD_ID), {
      wrapper,
    })
    expect(result.current.open).toBe(false)

    await act(async () => {
      await result.current.prompt("no_reply", "marked as no reply needed")
    })

    await waitFor(() => {
      expect(result.current.open).toBe(true)
    })
    expect(result.current.items).toHaveLength(1)
    expect(result.current.items[0]?.subject).toBe(SIBLING_SUBJECT)
    expect(result.current.treatment).toBe("no_reply")
    expect(result.current.reason).toBe("marked as no reply needed")
  })
})
