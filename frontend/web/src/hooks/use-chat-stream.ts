"use client"

import { useEffect, useRef, useState } from "react"

import { api } from "@/lib/api-client"
import type { ChatAskRequest } from "@/lib/types"
import type { ChatGroundedVerifier, ChatStreamHandlers } from "@/lib/chat-stream"

export type ChatStreamPhase = "idle" | "thinking" | "streaming" | "done" | "error"

export type ChatStreamAsk = (
  body: ChatAskRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
) => Promise<void>

type UseChatStreamOptions = {
  askStream?: ChatStreamAsk
}

export const useChatStream = (options: UseChatStreamOptions = {}) => {
  const askStream = options.askStream ?? api.chat.askStream
  const [phase, setPhase] = useState<ChatStreamPhase>("idle")
  const [status, setStatus] = useState<string | null>(null)
  const [assistantText, setAssistantText] = useState("")
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const stop = () => {
    abortRef.current?.abort()
  }

  const send = async (
    body: ChatAskRequest,
    handlers: ChatStreamHandlers = {},
  ): Promise<{ aborted: boolean }> => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setAssistantText("")
    setStatus(null)
    setPhase("thinking")
    try {
      await askStream(
        body,
        {
          onStatus: (text) => {
            setStatus(text)
            setPhase("thinking")
            handlers.onStatus?.(text)
          },
          onMeta: (meta) => {
            handlers.onMeta?.(meta)
          },
          onDelta: (text) => {
            if (text) {
              setPhase("streaming")
              setAssistantText((current) => `${current}${text}`)
            }
            handlers.onDelta?.(text)
          },
          onDone: (verdict) => {
            setStatus(null)
            setPhase("done")
            handlers.onDone?.(verdict)
          },
          onError: (message) => {
            setPhase("error")
            handlers.onError?.(message)
          },
        },
        controller.signal,
      )
      if (controller.signal.aborted) return { aborted: true }
      setPhase((current) => (current === "error" ? current : "done"))
      return { aborted: false }
    } catch (error) {
      if (controller.signal.aborted) {
        setPhase("idle")
        return { aborted: true }
      }
      setPhase("error")
      throw error
    }
  }

  return { phase, status, assistantText, send, stop }
}

export type { ChatGroundedVerifier }
