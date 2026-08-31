"use client"

import { useRef, type Dispatch, type SetStateAction } from "react"

import type { ChatTurn } from "@/components/chat-turn-list"
import { api } from "@/lib/api-client"
import { useChatStream } from "@/hooks/use-chat-stream"
import {
  clearStoredSessionId,
  readStoredSessionId,
  writeStoredSessionId,
} from "@/lib/chat-session-storage"
import { createRafDeltaBatcher } from "@/lib/stream-delta-batcher"
import { runInboxAssistantAsk, type PendingAskMeta } from "@/lib/inbox-assistant-ask"

type UseInboxAssistantChatArgs = {
  mailbox: string
  turns: ChatTurn[]
  setTurns: Dispatch<SetStateAction<ChatTurn[]>>
  sessionId: string | null
  setSessionId: Dispatch<SetStateAction<string | null>>
  setMessage: Dispatch<SetStateAction<string>>
  setValidation: Dispatch<SetStateAction<string | null>>
  setIsAsking: Dispatch<SetStateAction<boolean>>
  setToolStatus: Dispatch<SetStateAction<string | null>>
}

export const useInboxAssistantChat = ({
  mailbox,
  turns,
  setTurns,
  sessionId,
  setSessionId,
  setMessage,
  setValidation,
  setIsAsking,
  setToolStatus,
}: UseInboxAssistantChatArgs) => {
  const pendingMetaRef = useRef<PendingAskMeta | null>(null)
  const deltaBatcherRef = useRef<ReturnType<typeof createRafDeltaBatcher> | null>(null)
  const askGenerationRef = useRef(0)
  const stream = useChatStream()

  const resumeStoredSessionIfEmpty = async () => {
    if (turns.length > 0) return
    const stored = readStoredSessionId(mailbox)
    if (!stored) return
    try {
      const session = await api.chat.getSession(stored)
      setSessionId((current) => current ?? session.session_id)
      setTurns((current) => {
        if (current.length > 0) return current
        return session.messages.map((item, index) => ({
          id: `restored-${session.session_id}-${index}`,
          role: item.role,
          text: item.content,
        }))
      })
    } catch {
      clearStoredSessionId(mailbox)
    }
  }

  const ensureSessionId = async (): Promise<string> => {
    if (sessionId) return sessionId
    const created = await api.chat.createSession({
      mailbox: mailbox || null,
    })
    writeStoredSessionId(mailbox, created.session_id)
    setSessionId(created.session_id)
    return created.session_id
  }

  const handleReset = () => {
    stream.stop()
    pendingMetaRef.current = null
    clearStoredSessionId(mailbox)
    setSessionId(null)
    setToolStatus(null)
    setTurns([])
    setMessage("")
    setValidation(null)
    setIsAsking(false)
  }

  const handleStop = () => {
    stream.stop()
  }

  const handleAsk = async (nextMessage: string, options?: { bypassCache?: boolean }) => {
    await runInboxAssistantAsk({
      nextMessage,
      options,
      mailbox,
      turns,
      setTurns,
      setMessage,
      setValidation,
      setIsAsking,
      setToolStatus,
      pendingMetaRef,
      deltaBatcherRef,
      askGenerationRef,
      ensureSessionId,
      streamSend: (payload, handlers) => stream.send(payload, handlers),
    })
  }

  return {
    resumeStoredSessionIfEmpty,
    handleReset,
    handleStop,
    handleAsk,
  }
}
