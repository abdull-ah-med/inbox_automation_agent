"use client"

import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
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
import { getMutationErrorMessage } from "@/lib/error-messages"
import { formatReviewerDate } from "@/lib/dates"
import type { RelatedThreadItem, ThreadDetail } from "@/lib/types"
import { textLinkClass } from "@/lib/utils"

const MATCH_REASON_LABELS: Record<string, string> = {
  same_sender: "Same sender",
  same_subject: "Same subject",
  near_subject: "Similar subject",
  shared_deadline: "Shared deadline",
  cosine: "Similar content",
}

const isConfirmedAssociation = (item: RelatedThreadItem) => item.status === "confirmed"

const rowRemoveLabel = (item: RelatedThreadItem) =>
  isConfirmedAssociation(item) ? "Delete Association" : "Remove"

const rowRemoveAriaLabel = (item: RelatedThreadItem) =>
  isConfirmedAssociation(item)
    ? `Delete association ${item.subject}`
    : `Remove associated thread ${item.subject}`

const acceptBulkLabel = (allProposedSelected: boolean) =>
  allProposedSelected ? "Accept all" : "Accept"

const rejectBulkLabel = (allProposedSelected: boolean) =>
  allProposedSelected ? "Reject all" : "Reject"

const deleteBulkLabel = (allConfirmedSelected: boolean) =>
  allConfirmedSelected ? "Delete all associations" : "Delete Association"

const buildBulkToolbarState = (
  rows: RelatedThreadItem[],
  selectedIds: Set<string>,
  inflightIds: Set<string>,
) => {
  const proposedRows = rows.filter((item) => item.status !== "confirmed")
  const confirmedRows = rows.filter((item) => isConfirmedAssociation(item))
  const multiSelect = rows.length > 1
  const rowIds = rows.map((item) => item.thread_id)
  const proposedIds = proposedRows.map((item) => item.thread_id)
  const confirmedIds = confirmedRows.map((item) => item.thread_id)
  const selectedRowIds = rowIds.filter((id) => selectedIds.has(id))
  const selectedProposed = proposedIds.filter((id) => selectedIds.has(id))
  const selectedConfirmed = confirmedIds.filter((id) => selectedIds.has(id))
  const allSelected = multiSelect && rowIds.length > 0 && selectedRowIds.length === rowIds.length
  const someSelected = selectedRowIds.length > 0
  const partiallySelected = someSelected && !allSelected
  const bulkBusy = selectedRowIds.some((id) => inflightIds.has(id))
  const hasProposedSelected = selectedProposed.length > 0
  const hasConfirmedSelected = selectedConfirmed.length > 0
  const mixedSelection = hasProposedSelected && hasConfirmedSelected
  const allProposedSelected =
    proposedIds.length > 0 && selectedProposed.length === proposedIds.length
  const allConfirmedSelected =
    confirmedIds.length > 0 && selectedConfirmed.length === confirmedIds.length

  return {
    proposedRows,
    confirmedRows,
    multiSelect,
    rowIds,
    selectedProposed,
    selectedConfirmed,
    allSelected,
    partiallySelected,
    bulkAcceptLabel: acceptBulkLabel(allProposedSelected),
    bulkRejectLabel: rejectBulkLabel(allProposedSelected),
    bulkDeleteLabel: deleteBulkLabel(allConfirmedSelected),
    bulkProposedActionsDisabled: mixedSelection || !hasProposedSelected || bulkBusy,
    bulkConfirmedActionsDisabled: mixedSelection || !hasConfirmedSelected || bulkBusy,
  }
}

const MatchReasonChips = ({ reasons }: { reasons?: string[] | null }) => {
  if (!reasons || reasons.length === 0) return null
  return (
    <ul className="mt-1.5 flex flex-wrap gap-1" aria-label="Match reasons">
      {reasons.map((reason) => {
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
  )
}

const AssociationRow = ({
  item,
  multiSelect,
  selected,
  inflight,
  onPreview,
  onToggleSelected,
  onConfirm,
  onRemove,
}: {
  item: RelatedThreadItem
  multiSelect: boolean
  selected: boolean
  inflight: boolean
  onPreview: (relatedId: string) => void
  onToggleSelected: (relatedId: string) => void
  onConfirm: (relatedId: string) => void
  onRemove: (relatedId: string) => void
}) => {
  const isProposed = !isConfirmedAssociation(item)
  const removeLabel = rowRemoveLabel(item)
  const removeAriaLabel = rowRemoveAriaLabel(item)
  const showRowActions = !multiSelect || isProposed
  return (
    <li className="flex items-start justify-between gap-3 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        {multiSelect ? (
          <input
            type="checkbox"
            checked={selected}
            aria-label={`Select associated thread ${item.subject}`}
            tabIndex={0}
            className="mt-1 size-4 shrink-0"
            disabled={inflight}
            onChange={() => onToggleSelected(item.thread_id)}
          />
        ) : null}
        <div className="min-w-0 flex-1">
          <button
            type="button"
            tabIndex={0}
            aria-label={`Preview associated thread ${item.subject}`}
            className="block max-w-full truncate text-left text-sm font-medium text-blue-700 hover:underline dark:text-blue-400"
            onClick={() => onPreview(item.thread_id)}
          >
            {item.subject}
          </button>
          <p className="text-muted-foreground mt-0.5 truncate text-xs">
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700 dark:bg-gray-800 dark:text-gray-300">
              {item.mailbox}
            </span>
            {" · "}
            {formatReviewerDate(item.last_message_at)}
            {" · "}
            {item.status}
          </p>
          <MatchReasonChips reasons={item.match_reasons} />
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {showRowActions && isProposed ? (
          <Button
            type="button"
            size="sm"
            tabIndex={0}
            aria-label={`Confirm associated thread ${item.subject}`}
            disabled={inflight}
            onClick={() => onConfirm(item.thread_id)}
          >
            Confirm
          </Button>
        ) : null}
        {showRowActions ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            tabIndex={0}
            aria-label={removeAriaLabel}
            disabled={inflight}
            onClick={() => onRemove(item.thread_id)}
          >
            {removeLabel}
          </Button>
        ) : null}
      </div>
    </li>
  )
}

const AssociationPreviewDialog = ({
  open,
  sourceThreadId,
  sourceSubject,
  previewItem,
  previewId,
  isLoading,
  isError,
  error,
  data,
  onOpenChange,
  onBack,
}: {
  open: boolean
  sourceThreadId: string
  sourceSubject: string
  previewItem: RelatedThreadItem | null
  previewId: string | null
  isLoading: boolean
  isError: boolean
  error: unknown
  data: ThreadDetail | undefined
  onOpenChange: (open: boolean) => void
  onBack: () => void
}) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent size="lg" className="overscroll-contain">
      <DialogHeader>
        <DialogTitle className="pr-8 text-pretty">
          {previewItem?.subject ?? "Associated thread"}
        </DialogTitle>
        <DialogDescription>
          Still reviewing “{sourceSubject}”. Close this preview to return.
        </DialogDescription>
      </DialogHeader>
      {isLoading ? <p className="text-muted-foreground text-sm">Loading thread…</p> : null}
      {isError ? (
        <p className="text-sm text-red-600" role="alert">
          {error instanceof Error ? error.message : "Could not load associated thread."}
        </p>
      ) : null}
      {data ? (
        <div className="max-h-[50vh] overflow-y-auto overscroll-contain">
          <ThreadEmailPanel
            threadId={previewId ?? data.thread.id}
            subject={data.thread.subject}
            messages={data.messages}
          />
        </div>
      ) : null}
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          tabIndex={0}
          aria-label="Back to current thread"
          onClick={onBack}
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
)

export const AssociatedThreadsList = (props: {
  sourceThreadId: string
  sourceSubject: string
  items: RelatedThreadItem[]
}) => <AssociatedThreadsListState key={props.sourceThreadId} {...props} />

const AssociatedThreadsListState = ({
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
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(() => new Set())
  const [statusById, setStatusById] = useState<
    Partial<Record<string, RelatedThreadItem["status"]>>
  >({})
  const [inflightIds, setInflightIds] = useState<Set<string>>(() => new Set())
  const inflightRef = useRef(new Map<string, AbortController>())
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())

  const rows = items
    .filter((item) => !dismissedIds.has(item.thread_id))
    .map((item) => {
      const status = statusById[item.thread_id]
      return status ? { ...item, status } : item
    })

  const {
    proposedRows,
    confirmedRows,
    multiSelect,
    rowIds,
    selectedProposed,
    selectedConfirmed,
    allSelected,
    partiallySelected,
    bulkAcceptLabel,
    bulkRejectLabel,
    bulkDeleteLabel,
    bulkProposedActionsDisabled,
    bulkConfirmedActionsDisabled,
  } = buildBulkToolbarState(rows, selectedIds, inflightIds)

  const previewQuery = useQuery({
    queryKey: ["thread", previewId],
    queryFn: () => api.threads.detail(previewId ?? ""),
    enabled: previewId != null,
  })

  if (rows.length === 0) {
    return null
  }

  const previewItem = rows.find((item) => item.thread_id === previewId) ?? null

  const handleReview = async (relatedId: string, status: "confirmed" | "dismissed") => {
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
      if (nextStatus === "dismissed") {
        setDismissedIds((current) => {
          const next = new Set(current)
          next.add(relatedId)
          return next
        })
      } else {
        setStatusById((current) => ({ ...current, [relatedId]: nextStatus }))
      }
      setSelectedIds((current) => {
        if (!current.has(relatedId)) return current
        const next = new Set(current)
        next.delete(relatedId)
        return next
      })
      void queryClient.invalidateQueries({ queryKey: ["thread", sourceThreadId] })
      if (nextStatus === "dismissed" && previewId === relatedId) {
        setPreviewId(null)
      }
    } catch (err) {
      if (controller.signal.aborted) {
        return
      }
      setError(getMutationErrorMessage(err, "Could not update association."))
    } finally {
      inflightRef.current.delete(relatedId)
      setInflightIds(new Set(inflightRef.current.keys()))
    }
  }

  const handleToggleSelected = (relatedId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(relatedId)) {
        next.delete(relatedId)
      } else {
        next.add(relatedId)
      }
      return next
    })
  }

  const handleToggleSelectAll = () => {
    setSelectedIds((current) => {
      const everySelected = rowIds.every((id) => current.has(id))
      if (everySelected) return new Set()
      return new Set(rowIds)
    })
  }

  const handleConfirmSelected = () => {
    if (selectedProposed.length === 0) return
    for (const relatedId of selectedProposed) {
      void handleReview(relatedId, "confirmed")
    }
  }

  const handleRemoveSelectedProposed = () => {
    if (selectedProposed.length === 0) return
    for (const relatedId of selectedProposed) {
      void handleReview(relatedId, "dismissed")
    }
  }

  const handleDeleteSelectedConfirmed = () => {
    if (selectedConfirmed.length === 0) return
    for (const relatedId of selectedConfirmed) {
      void handleReview(relatedId, "dismissed")
    }
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
      {multiSelect ? (
        <div className="mb-2 flex items-center justify-between gap-3 px-3">
          <label className="flex items-center gap-3">
            <input
              ref={(el) => {
                if (el) el.indeterminate = partiallySelected
              }}
              type="checkbox"
              checked={allSelected}
              aria-checked={allSelected ? true : partiallySelected ? "mixed" : false}
              aria-label="Select all associations"
              tabIndex={0}
              className="size-4 shrink-0"
              onChange={handleToggleSelectAll}
            />
            <span className="text-muted-foreground text-xs">Select all</span>
          </label>
          <div className="flex items-center gap-1">
            {proposedRows.length > 0 ? (
              <>
                <Button
                  type="button"
                  size="sm"
                  tabIndex={0}
                  aria-label={bulkAcceptLabel}
                  disabled={bulkProposedActionsDisabled}
                  onClick={handleConfirmSelected}
                >
                  {bulkAcceptLabel}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  tabIndex={0}
                  aria-label={bulkRejectLabel}
                  disabled={bulkProposedActionsDisabled}
                  onClick={handleRemoveSelectedProposed}
                >
                  {bulkRejectLabel}
                </Button>
              </>
            ) : null}
            {confirmedRows.length > 0 ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                tabIndex={0}
                aria-label={bulkDeleteLabel}
                disabled={bulkConfirmedActionsDisabled}
                onClick={handleDeleteSelectedConfirmed}
              >
                {bulkDeleteLabel}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
      <ul className="space-y-2">
        {rows.map((item) => (
          <AssociationRow
            key={item.thread_id}
            item={item}
            multiSelect={multiSelect}
            selected={selectedIds.has(item.thread_id)}
            inflight={inflightIds.has(item.thread_id)}
            onPreview={setPreviewId}
            onToggleSelected={handleToggleSelected}
            onConfirm={(relatedId) => {
              void handleReview(relatedId, "confirmed")
            }}
            onRemove={(relatedId) => {
              void handleReview(relatedId, "dismissed")
            }}
          />
        ))}
      </ul>
      <AssociationPreviewDialog
        open={previewId != null}
        sourceThreadId={sourceThreadId}
        sourceSubject={sourceSubject}
        previewItem={previewItem}
        previewId={previewId}
        isLoading={previewQuery.isLoading}
        isError={previewQuery.isError}
        error={previewQuery.error}
        data={previewQuery.data}
        onOpenChange={(open) => {
          if (!open) setPreviewId(null)
        }}
        onBack={() => setPreviewId(null)}
      />
    </section>
  )
}
