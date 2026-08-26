import { act, renderHook, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { useChatStream } from "@/hooks/use-chat-stream"
import type { ChatStreamHandlers } from "@/lib/chat-stream"

describe("useChatStream", () => {
  it("moves idle → thinking → streaming → done and shows the answer", async () => {
    let releaseStatus: (() => void) | undefined
    const afterStatus = new Promise<void>((resolve) => {
      releaseStatus = resolve
    })
    const askStream = vi.fn(
      async (
        _body: unknown,
        handlers: ChatStreamHandlers,
      ) => {
        handlers.onStatus?.("Searching mail")
        await afterStatus
        handlers.onDelta?.("Focus on ")
        handlers.onDelta?.("billing.")
        handlers.onDone?.("SUPPORTED")
      },
    )

    const { result } = renderHook(() => useChatStream({ askStream }))
    expect(result.current.phase).toBe("idle")
    expect(result.current.assistantText).toBe("")

    let sendPromise: Promise<{ aborted: boolean }> | undefined
    act(() => {
      sendPromise = result.current.send({
        message: "billing disputes waiting on review",
      })
    })

    await waitFor(() => {
      expect(result.current.phase).toBe("thinking")
    })
    expect(result.current.status).toBe("Searching mail")
    expect(result.current.assistantText).toBe("")

    await act(async () => {
      releaseStatus?.()
      await sendPromise
    })

    expect(result.current.phase).toBe("done")
    expect(result.current.assistantText).toBe("Focus on billing.")
    expect(result.current.status).toBeNull()
  })
})
