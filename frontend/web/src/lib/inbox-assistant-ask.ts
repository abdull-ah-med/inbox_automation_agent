import type { Dispatch, MutableRefObject, SetStateAction } from "react"

import type { ChatTurn } from "@/components/chat-turn-list"
import { getErrorMessage } from "@/lib/error-messages"
import { sanitizeUserText } from "@/lib/sanitize"
import type { ChatAskRequest, ChatCitation, ChatHistoryTurn } from "@/lib/types"
import type { ChatGroundedVerifier } from "@/lib/chat-stream"
import { toChatHistoryPayload } from "@/lib/chat-history"
import { createRafDeltaBatcher } from "@/lib/stream-delta-batcher"

export type PendingAskMeta = {
  citations: ChatCitation[]
  refusedWrite: boolean
  cached: boolean
  groundedVerifier?: ChatGroundedVerifier
}

type StreamSend = (
  payload: ChatAskRequest,
  handlers: {
    onStatus?: (text: string) => void
    onMeta?: (meta: {
      citations: ChatCitation[]
      refused_write: boolean
      cached?: boolean
      grounded_verifier?: ChatGroundedVerifier
    }) => void
    onDelta?: (text: string) => void
    onDone?: (verdict?: ChatGroundedVerifier) => void
  },
) => Promise<{ aborted: boolean }>

type RunInboxAssistantAskArgs = {
  nextMessage: string
  options?: { bypassCache?: boolean }
  mailbox: string
  turns: ChatTurn[]
  setTurns: Dispatch<SetStateAction<ChatTurn[]>>
  setMessage: Dispatch<SetStateAction<string>>
  setValidation: Dispatch<SetStateAction<string | null>>
  setIsAsking: Dispatch<SetStateAction<boolean>>
  setToolStatus: Dispatch<SetStateAction<string | null>>
  pendingMetaRef: MutableRefObject<PendingAskMeta | null>
  deltaBatcherRef: MutableRefObject<ReturnType<typeof createRafDeltaBatcher> | null>
  askGenerationRef: MutableRefObject<number>
  ensureSessionId: () => Promise<string>
  streamSend: StreamSend
}

export const runInboxAssistantAsk = async ({
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
  streamSend,
}: RunInboxAssistantAskArgs): Promise<void> => {
  const trimmed = sanitizeUserText(nextMessage)
  if (!trimmed) {
    setValidation("Enter a question")
    return
  }
  setValidation(null)
  setMessage("")
  const generation = askGenerationRef.current + 1
  askGenerationRef.current = generation
  const history: ChatHistoryTurn[] = turns
    .filter((turn) => !turn.error && turn.text.trim())
    .map((turn) => {
      if (turn.role !== "assistant" || !turn.citations?.length) {
        return { role: turn.role, content: turn.text }
      }
      return {
        role: turn.role,
        content: turn.text,
        citations: turn.citations.map((citation) => ({
          thread_id: citation.thread_id,
          subject: citation.subject,
        })),
      }
    })
  const userTurn: ChatTurn = {
    id: `user-${crypto.randomUUID()}`,
    role: "user",
    text: trimmed,
  }
  setTurns((current) => [...current, userTurn])
  const assistantId = `assistant-${crypto.randomUUID()}`
  setToolStatus(null)
  setIsAsking(true)
  let receivedAnswer = false
  deltaBatcherRef.current?.flushNow()
  const deltaBatcher = createRafDeltaBatcher((chunk) => {
    setTurns((current) => {
      const existing = current.find((turn) => turn.id === assistantId)
      const pendingCitations = pendingMetaRef.current?.citations
      if (existing) {
        return current.map((turn) =>
          turn.id === assistantId
            ? {
                ...turn,
                text: `${turn.text}${chunk}`,
                streaming: true,
                citations: turn.citations ?? pendingCitations,
              }
            : turn,
        )
      }
      return [
        ...current,
        {
          id: assistantId,
          role: "assistant",
          text: chunk,
          streaming: true,
          citations: pendingCitations,
        },
      ]
    })
  })
  deltaBatcherRef.current = deltaBatcher
  try {
    const activeSessionId = await ensureSessionId()
    const payload: ChatAskRequest = {
      message: trimmed,
      session_id: activeSessionId,
    }
    if (mailbox) payload.mailbox = mailbox
    if (history.length > 0) payload.history = toChatHistoryPayload(history)
    if (options?.bypassCache) payload.bypass_cache = true
    const result = await streamSend(payload, {
      onStatus: (text) => {
        setToolStatus(text)
      },
      onMeta: (meta) => {
        pendingMetaRef.current = {
          citations: meta.citations,
          refusedWrite: meta.refused_write,
          cached: Boolean(meta.cached),
          groundedVerifier: meta.grounded_verifier,
        }
        setTurns((current) =>
          current.map((turn) =>
            turn.id === assistantId
              ? {
                  ...turn,
                  citations: meta.citations,
                  refusedWrite: meta.refused_write,
                  cached: Boolean(meta.cached),
                  groundedVerifier: meta.grounded_verifier,
                }
              : turn,
          ),
        )
      },
      onDelta: (text) => {
        if (text) receivedAnswer = true
        deltaBatcher.push(text)
      },
      onDone: (verdict) => {
        applyAskDone({
          assistantId,
          trimmed,
          verdict,
          deltaBatcher,
          pendingMetaRef,
          setToolStatus,
          setTurns,
        })
      },
    })
    deltaBatcher.flushNow()
    if (result.aborted) {
      if (askGenerationRef.current === generation) {
        pendingMetaRef.current = null
        setToolStatus(null)
        setTurns((current) =>
          current.map((turn) => (turn.id === assistantId ? { ...turn, streaming: false } : turn)),
        )
      }
      return
    }
    if (!receivedAnswer) {
      throw new Error("InboxAssistant did not return an answer. Please try again.")
    }
  } catch (error) {
    deltaBatcher.flushNow()
    if (askGenerationRef.current !== generation) return
    pendingMetaRef.current = null
    setToolStatus(null)
    setTurns((current) => [
      ...current.map((turn) => (turn.id === assistantId ? { ...turn, streaming: false } : turn)),
      {
        id: `error-${crypto.randomUUID()}`,
        role: "assistant",
        text: getErrorMessage(error),
        error: true,
      },
    ])
  } finally {
    if (deltaBatcherRef.current === deltaBatcher) {
      deltaBatcherRef.current = null
    }
    if (askGenerationRef.current === generation) {
      pendingMetaRef.current = null
      setIsAsking(false)
    }
  }
}

const applyAskDone = ({
  assistantId,
  trimmed,
  verdict,
  deltaBatcher,
  pendingMetaRef,
  setToolStatus,
  setTurns,
}: {
  assistantId: string
  trimmed: string
  verdict?: ChatGroundedVerifier
  deltaBatcher: ReturnType<typeof createRafDeltaBatcher>
  pendingMetaRef: MutableRefObject<PendingAskMeta | null>
  setToolStatus: Dispatch<SetStateAction<string | null>>
  setTurns: Dispatch<SetStateAction<ChatTurn[]>>
}) => {
  const trailing = deltaBatcher.drain()
  const pending = pendingMetaRef.current
  pendingMetaRef.current = null
  setToolStatus(null)
  const citations = pending?.citations ?? []
  const refusedWrite = pending?.refusedWrite ?? false
  const cached = pending?.cached ?? false
  const groundedVerifier = verdict ?? pending?.groundedVerifier
  setTurns((current) => {
    const existing = current.find((turn) => turn.id === assistantId)
    if (existing) {
      return current.map((turn) =>
        turn.id === assistantId
          ? {
              ...turn,
              text: trailing ? `${turn.text}${trailing}` : turn.text,
              citations,
              refusedWrite,
              cached,
              groundedVerifier,
              lastQuestion: trimmed,
              streaming: false,
            }
          : turn,
      )
    }
    if (!trailing && citations.length === 0) {
      return current
    }
    return [
      ...current,
      {
        id: assistantId,
        role: "assistant",
        text: trailing,
        citations,
        refusedWrite,
        cached,
        groundedVerifier,
        lastQuestion: trimmed,
        streaming: false,
      },
    ]
  })
}
