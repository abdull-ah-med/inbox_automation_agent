"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { api } from "@/lib/api-client"
import type { RelatedThreadItem } from "@/lib/types"

export type SiblingTreatment = "no_reply" | "urgency"
export type SiblingUrgency = "CRITICAL" | "HIGH" | "NORMAL" | "LOW"

export const siblingsQueryKey = (threadId: string) =>
  ["thread", threadId, "related", "siblings"] as const

export const useSiblingsPrompt = (threadId: string) => {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<RelatedThreadItem[]>([])
  const [treatment, setTreatment] = useState<SiblingTreatment>("no_reply")
  const [reason, setReason] = useState("")
  const [urgency, setUrgency] = useState<SiblingUrgency | undefined>(undefined)

  const prompt = async (
    nextTreatment: SiblingTreatment,
    nextReason: string,
    nextUrgency?: SiblingUrgency,
  ) => {
    try {
      const data = await queryClient.fetchQuery({
        queryKey: siblingsQueryKey(threadId),
        queryFn: () => api.threads.related(threadId, "siblings"),
      })
      if (data.items.length === 0) return
      setItems(data.items)
      setTreatment(nextTreatment)
      setReason(nextReason)
      setUrgency(nextUrgency)
      setOpen(true)
    } catch {
      // Source action already succeeded; an empty prompt is the fallback.
    }
  }

  return {
    open,
    setOpen,
    items,
    treatment,
    reason,
    urgency,
    prompt,
  }
}
