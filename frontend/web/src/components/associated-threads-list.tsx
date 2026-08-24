"use client"

import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { X } from "lucide-react"
import { useRef, useState } from "react"

import { ThreadEmailPanel } from "@/components/thread-email-panel"
import { associatedThreadHref } from "@/components/thread-origin-banner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api-client"
import { formatReviewerDate } from "@/lib/dates"
import type { RelatedThreadItem } from "@/lib/types"
import { textLinkClass } from "@/lib/utils"

const MATCH_REASON_LABELS: Record<string, string> = {
  same_sender: "Same sender",
  same_subject: "Same subject",
  near_subject: "Similar subject",
  shared_deadline: "Shared deadline",
  cosine: "Similar content",
}

export const AssociatedThreadsList = ({
  sourceThreadId,
  sourceSubject,
  items,
}: {
  sourceThreadId: string
  sourceSubject: string
  items: RelatedThreadItem[]
}) => {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [rows, setRows] = useState(items)
  const [itemSnapshot, setItemSnapshot] = useState(items)
  const [inflightIds, setInflightIds] = useState<Set<string>>(() => new Set())
  const inflightRef = useRef(new Map<string, AbortController>())
  const [previewId, setPreviewId] = useState<string | null>(null)

  if (items !== itemSnapshot) {
    setItemSnapshot(items)
    setRows(items)
  }

  const previewQuery = useQuery({
    queryKey: ["thread", previewId],
    queryFn: () => api.threads.detail(previewId ?? ""),
    enabled: previewId != null,
  })

  if (rows.length === 0) {
    return null
  }

  const previewItem = rows.find((item) => item.thread_id === previewId) ?? null

  const handleReview = async (
    relatedId: string,
    status: "confirmed" | "dismissed",
  ) => {
    if (inflightRef.current.has(relatedId)) {
      return
    }
    const controller = new AbortController()
    inflightRef.current.set(relatedId, controller)
    setInflightIds(new Set(inflightRef.current.keys()))
    setError(null)
    try {
      const result = await api.threads.reviewRelated(
        sourceThreadId,
        relatedId,
        { status },
        controller.signal,
      )
      if (controller.signal.aborted) {
        return
      }
      inflightRef.current.delete(relatedId)
      setInflightIds(new Set(inflightRef.current.keys()))
      const nextStatus = result.status
      setRows((current) => {
        if (nextStatus === "dismissed") {
          return current.filter((item) => item.thread_id !== relatedId)
        }
        return current.map((item) =>
          item.thread_id === relatedId ? { ...item, status: nextStatus } : item,
        )
      })
      void queryClient.invalidateQueries({ queryKey: ["thread", sourceThreadId] })
      if (nextStatus === "dismissed" && previewId === relatedId) {
        setPreviewId(null)
      }
    } catch (err) {
      if (controller.signal.aborted) {
        return
      }
      setError(err instanceof Error ? err.message : "Could not update association.")
    } finally {
      inflightRef.current.delete(relatedId)
      setInflightIds(new Set(inflightRef.current.keys()))
    }
  }

  const handlePreview = (relatedId: string) => {
    setPreviewId(relatedId)
  }

  const handlePreviewOpenChange = (open: boolean) => {
    if (!open) {
      setPreviewId(null)
    }
  }

  const handleBackToCurrent = () => {
    setPreviewId(null)
  }

  return (
    <section aria-label="Associated threads" className="mb-5">
      <h2 className="mb-2 text-sm font-semibold text-gray-900 dark:text-gray-100">
        Associated threads
      </h2>
      {error ? (
        <p className="mb-2 text-sm text-red-600" role="alert">
          {error}
        </p>
      ) : null}
      <ul className="space-y-2">
        {rows.map((item) => (
          <li
            key={item.thread_id}
            className="flex items-start justify-between gap-3 rounded-lg border border-gray-200 p-3 dark:border-gray-800"
          >
            <div className="min-w-0 flex-1">
              <button
                type="button"
                tabIndex={0}
                aria-label={`Preview associated thread ${item.subject}`}
                className="block max-w-full truncate text-left text-sm font-medium text-blue-700 hover:underline dark:text-blue-400"
                onClick={() => handlePreview(item.thread_id)}
              >
                {item.subject}
              </button>
              <p className="mt-0.5 truncate text-xs text-gray-500">
                <span className="rounded bg-gray-100 px-1.5 py-0.5 dark:bg-gray-800">
                  {item.mailbox}
                </span>
                {" · "}
                {formatReviewerDate(item.last_message_at)}
                {" · "}
                {item.status}
              </p>
              {item.match_reasons && item.match_reasons.length > 0 ? (
                <ul className="mt-1.5 flex flex-wrap gap-1" aria-label="Match reasons">
                  {item.match_reasons.map((reason) => {
                    const label = MATCH_REASON_LABELS[reason]
                    if (!label) return null
                    return (
                      <li
                        key={reason}
                        className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                      >
                        {label}
                      </li>
                    )
                  })}
                </ul>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {item.status !== "confirmed" ? (
                <Button
                  type="button"
                  size="sm"
                  tabIndex={0}
                  aria-label={`Confirm associated thread ${item.subject}`}
                  disabled={inflightIds.has(item.thread_id)}
                  onClick={() => {
                    void handleReview(item.thread_id, "confirmed")
                  }}
                >
                  Confirm
                </Button>
              ) : null}
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                tabIndex={0}
                aria-label={`Dismiss associated thread ${item.subject}`}
                disabled={inflightIds.has(item.thread_id)}
                onClick={() => {
                  void handleReview(item.thread_id, "dismissed")
                }}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <Dialog open={previewId != null} onOpenChange={handlePreviewOpenChange}>
        <DialogContent size="lg" className="overscroll-contain">
          <DialogHeader>
            <DialogTitle className="text-pretty pr-8">
              {previewItem?.subject ?? "Associated thread"}
            </DialogTitle>
            <DialogDescription>
              Still reviewing “{sourceSubject}”. Close this preview to return.
            </DialogDescription>
          </DialogHeader>
          {previewQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading thread…</p>
          ) : null}
          {previewQuery.isError ? (
            <p className="text-sm text-red-600" role="alert">
              {previewQuery.error instanceof Error
                ? previewQuery.error.message
                : "Could not load associated thread."}
            </p>
          ) : null}
          {previewQuery.data ? (
            <div className="max-h-[50vh] overflow-y-auto overscroll-contain">
              <ThreadEmailPanel
                subject={previewQuery.data.thread.subject}
                messages={previewQuery.data.messages}
              />
            </div>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Back to current thread"
              onClick={handleBackToCurrent}
            >
              Back to current thread
            </Button>
            {previewId ? (
              <Link
                href={associatedThreadHref(previewId, sourceThreadId)}
                className={textLinkClass}
                aria-label="Open full thread"
              >
                Open full thread
              </Link>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}
