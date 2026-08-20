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
      new Response(
        'data: {"type":"delta","text":"Hi"}\n\ndata: {"type":"done"}\n\n',
        {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        },
      ),
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
    await expect(
      api.chat.askStream({ message: "billing disputes" }, {}),
    ).rejects.toMatchObject({ status: 502 })
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
})
