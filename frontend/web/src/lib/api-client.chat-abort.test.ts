import { beforeEach, describe, expect, it, vi } from "vitest"

import { clearAuthSession, setAuthSession } from "@/features/auth/auth-store"
import { api } from "@/lib/api-client"

describe("api.chat.askStream abort", () => {
  beforeEach(() => {
    clearAuthSession()
    vi.restoreAllMocks()
    setAuthSession({
      accessToken: "tok",
      expiresIn: 900,
      user: {
        id: "1",
        email: "elise@example.com",
        role: "user",
        created_at: new Date().toISOString(),
      },
    })
  })

  it("passes the abort signal to fetch", async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('data: {"type":"delta","text":"Hi"}\n\ndata: {"type":"done"}\n\n', {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await api.chat.askStream(
      { message: "billing disputes waiting on review" },
      {},
      controller.signal,
    )

    expect(fetchMock).toHaveBeenCalled()
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.signal).toBe(controller.signal)
  })

  it("retries a connection failure before the first delta", async () => {
    vi.useFakeTimers()
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(
        new Response('data: {"type":"delta","text":"Hi"}\n\ndata: {"type":"done"}\n\n', {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      )
    vi.stubGlobal("fetch", fetchMock)
    const pending = api.chat.askStream({ message: "billing disputes" }, {})
    await vi.runAllTimersAsync()
    await pending
    expect(fetchMock).toHaveBeenCalledTimes(2)
    vi.useRealTimers()
  })

  it("does not retry after a delta has already streamed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        'data: {"type":"delta","text":"Hi"}\n\ndata: {"type":"error","message":"Claude chat failed","partial":true}\n\n',
        {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)
    await expect(api.chat.askStream({ message: "billing disputes" }, {})).rejects.toMatchObject({
      status: 502,
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("rejects when the stream closes with no answer", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("", {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    await expect(
      api.chat.askStream({ message: "give me the latest on omason" }, {}),
    ).rejects.toMatchObject({ status: 502 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("rejects when the stream sends done without a delta", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('data: {"type":"done"}\n\n', {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    await expect(
      api.chat.askStream({ message: "give me the latest on omason" }, {}),
    ).rejects.toMatchObject({ status: 502 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("cancels the response reader exactly once when the caller aborts mid-stream", async () => {
    const controller = new AbortController()
    let cancelCalls = 0
    const stream = new ReadableStream({
      start(streamController) {
        streamController.enqueue(new TextEncoder().encode('data: {"type":"delta","text":"Hi"}\n\n'))
        // Deliberately never enqueue "done" or close — simulates an in-flight SSE body.
      },
      cancel() {
        cancelCalls += 1
      },
    })
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    const pending = api.chat
      .askStream({ message: "billing disputes waiting on review" }, {}, controller.signal)
      .catch(() => {
        // Aborting rejects the call; the cancellation side effect is what this test verifies.
      })

    await vi.waitFor(() => expect(cancelCalls).toBe(0))
    controller.abort()
    await pending

    expect(cancelCalls).toBe(1)
  })

  it("wakes the retry backoff immediately when aborted instead of waiting out the jitter", async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      const signal = init?.signal as AbortSignal | undefined
      if (signal?.aborted) {
        return Promise.reject(new DOMException("aborted", "AbortError"))
      }
      return Promise.reject(new TypeError("network"))
    })
    vi.stubGlobal("fetch", fetchMock)

    const started = Date.now()
    const pending = api.chat.askStream({ message: "billing disputes" }, {}, controller.signal)
    // Abort well before the ~80-320ms jitter window would naturally elapse.
    setTimeout(() => controller.abort(), 5)

    await expect(pending).rejects.toBeTruthy()
    expect(Date.now() - started).toBeLessThan(60)
  })
})
