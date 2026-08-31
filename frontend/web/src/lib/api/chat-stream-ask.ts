import { getAccessToken } from "@/features/auth/auth-store"
import { messageForStatus } from "@/lib/error-messages"
import { dispatchChatStreamBlock, type ChatStreamHandlers } from "@/lib/chat-stream"
import { API_BASE, ApiError, buildAuthHeaders, refreshAccessToken } from "@/lib/api/client"
import type { ChatAskRequest } from "@/lib/types"

const jitter = (attempt: number) => 80 * 2 ** attempt + Math.floor(Math.random() * 40)

const isAbortError = (error: unknown, signal?: AbortSignal): boolean => {
  if (signal?.aborted) return true
  if (error instanceof DOMException && error.name === "AbortError") return true
  return error instanceof Error && error.name === "AbortError"
}

const waitForRetry = async (attempt: number, signal?: AbortSignal): Promise<void> => {
  await new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, jitter(attempt))
    if (!signal) return
    const wakeEarly = () => {
      window.clearTimeout(timer)
      resolve()
    }
    signal.addEventListener("abort", wakeEarly, { once: true })
  })
}

const postAskStream = async (
  body: ChatAskRequest,
  headers: Headers,
  signal?: AbortSignal,
): Promise<Response> => {
  const post = (requestHeaders: Headers) =>
    fetch(`${API_BASE}/api/chat/ask/stream`, {
      method: "POST",
      headers: requestHeaders,
      credentials: "include",
      body: JSON.stringify(body),
      signal,
    })

  const resp = await post(headers)
  if (resp.status !== 401) return resp

  const ok = await refreshAccessToken()
  if (!ok) return resp

  const retryHeaders = new Headers(headers)
  const nextToken = getAccessToken()
  if (nextToken) {
    retryHeaders.set("Authorization", `Bearer ${nextToken}`)
  }
  return post(retryHeaders)
}

const throwIfNotOk = async (resp: Response): Promise<void> => {
  if (resp.ok) return
  let errorBody: unknown
  try {
    errorBody = await resp.json()
  } catch {
    errorBody = undefined
  }
  throw new ApiError(messageForStatus(resp.status), resp.status, errorBody)
}

const dispatchParts = (parts: string[], handlers: ChatStreamHandlers): void => {
  for (const part of parts) {
    const status = dispatchChatStreamBlock(part, handlers)
    if (status === "error") {
      throw new ApiError("Claude chat failed", 502)
    }
  }
}

type ConsumeResult = { sawDelta: boolean; sawError: boolean }

const releaseReader = async (reader: ReadableStreamDefaultReader<Uint8Array>): Promise<void> => {
  try {
    reader.releaseLock()
  } catch {
    // Reader may already be released after cancel.
  }
}

type ReadChunkResult =
  | { kind: "read"; chunk: ReadableStreamReadResult<Uint8Array> }
  | { kind: "abort" }

const readChunkWithAbort = async (
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal?: AbortSignal,
): Promise<ReadChunkResult> => {
  if (signal?.aborted) {
    await reader.cancel().catch(() => {})
    return { kind: "abort" }
  }
  if (!signal) {
    return { kind: "read", chunk: await reader.read() }
  }

  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (result: ReadChunkResult) => {
      if (settled) return
      settled = true
      signal.removeEventListener("abort", onAbortDuringRead)
      resolve(result)
    }
    const onAbortDuringRead = () => {
      void reader.cancel().catch(() => {})
      finish({ kind: "abort" })
    }
    signal.addEventListener("abort", onAbortDuringRead)
    reader.read().then(
      (chunk) => finish({ kind: "read", chunk }),
      (error) => {
        if (settled) return
        settled = true
        signal.removeEventListener("abort", onAbortDuringRead)
        reject(error)
      },
    )
  })
}

const consumeAskStreamBody = async (
  body: ReadableStream<Uint8Array>,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<ConsumeResult> => {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let sawDelta = false
  let sawError = false
  let aborted = false
  const wrapped: ChatStreamHandlers = {
    ...handlers,
    onDelta: (text) => {
      if (text) sawDelta = true
      handlers.onDelta?.(text)
    },
    onError: (message) => {
      sawError = true
      handlers.onError?.(message)
    },
  }
  try {
    while (!aborted) {
      let chunk: ReadableStreamReadResult<Uint8Array>
      try {
        const result = await readChunkWithAbort(reader, signal)
        if (result.kind === "abort") {
          aborted = true
          break
        }
        chunk = result.chunk
      } catch (error) {
        if (isAbortError(error, signal)) {
          aborted = true
          break
        }
        throw error
      }
      const { done, value } = chunk
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split("\n\n")
      buffer = parts.pop() ?? ""
      dispatchParts(parts, wrapped)
    }
    if (!aborted && buffer.trim()) {
      dispatchParts([buffer], wrapped)
    }
    if (aborted || signal?.aborted) {
      throw new DOMException("The operation was aborted", "AbortError")
    }
    return { sawDelta, sawError }
  } finally {
    if (!aborted) {
      await releaseReader(reader)
    }
  }
}

export const askStreamOnce = async (
  body: ChatAskRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> => {
  const headers = buildAuthHeaders(undefined, "application/json")
  headers.set("Accept", "text/event-stream")

  const resp = await postAskStream(body, headers, signal)
  await throwIfNotOk(resp)
  if (!resp.body) {
    throw new ApiError(messageForStatus(502), 502)
  }

  const { sawDelta, sawError } = await consumeAskStreamBody(resp.body, handlers, signal)
  if (sawError) {
    throw new ApiError("Claude chat failed", 502)
  }
  if (!sawDelta) {
    throw new ApiError(messageForStatus(502), 502)
  }
}

export const askStreamWithRetry = async (
  body: ChatAskRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> => {
  let sawDelta = false
  let lastError: unknown
  const trackingHandlers: ChatStreamHandlers = {
    ...handlers,
    onDelta: (text) => {
      if (text) sawDelta = true
      handlers.onDelta?.(text)
    },
  }

  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (attempt > 0 && sawDelta) break
    let streamFinished = false
    try {
      await askStreamOnce(body, trackingHandlers, signal)
      streamFinished = true
      return
    } catch (error) {
      lastError = error
      const aborted = isAbortError(error, signal)
      const terminal =
        aborted || sawDelta || streamFinished || attempt === 2 || error instanceof ApiError
      if (terminal) {
        throw error
      }
      await waitForRetry(attempt, signal)
    }
  }

  throw lastError instanceof Error ? lastError : new ApiError(messageForStatus(502), 502)
}
