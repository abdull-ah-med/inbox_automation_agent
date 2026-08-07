"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Pencil } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Select } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api-client"

const URGENCY_LEVELS = ["CRITICAL", "HIGH", "NORMAL", "LOW"] as const

type UrgencyLevel = (typeof URGENCY_LEVELS)[number]

export const UrgencyEditPopover = ({
  draftId,
  threadId,
  currentUrgency,
  disabled = false,
}: {
  draftId: string
  threadId: string
  currentUrgency: string | null
  disabled?: boolean
}) => {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [urgency, setUrgency] = useState<UrgencyLevel>(
    (URGENCY_LEVELS.includes(currentUrgency as UrgencyLevel)
      ? currentUrgency
      : "NORMAL") as UrgencyLevel,
  )
  const [reason, setReason] = useState("")
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      api.drafts.editUrgency(draftId, {
        new_urgency: urgency,
        reason: reason.trim(),
      }),
    onSuccess: async () => {
      setOpen(false)
      setReason("")
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const handleOpen = () => {
    setUrgency(
      (URGENCY_LEVELS.includes(currentUrgency as UrgencyLevel)
        ? currentUrgency
        : "NORMAL") as UrgencyLevel,
    )
    setReason("")
    setError(null)
    setOpen(true)
  }

  const handleOpenKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleOpen()
    }
  }

  const handleSave = () => {
    if (!reason.trim()) return
    mutation.mutate()
  }

  return (
    <>
      <Button
        type="button"
        size="icon-xs"
        variant="ghost"
        tabIndex={0}
        aria-label="Edit urgency"
        disabled={disabled || mutation.isPending}
        onClick={handleOpen}
        onKeyDown={handleOpenKeyDown}
        className="ml-1"
      >
        <Pencil aria-hidden="true" />
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Edit urgency</DialogTitle>
            <DialogDescription>
              Change the urgency level and explain why. This teaches future
              drafts about off-thread or business context.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500" htmlFor="urgency-level">
                Urgency level
              </label>
              <Select
                id="urgency-level"
                name="urgency_level"
                value={urgency}
                onChange={(event) =>
                  setUrgency(event.target.value as UrgencyLevel)
                }
                aria-label="Urgency level"
                className="mt-1"
              >
                {URGENCY_LEVELS.map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="text-xs text-gray-500" htmlFor="urgency-reason">
                Why is this the right urgency?
              </label>
              <Textarea
                id="urgency-reason"
                name="urgency_reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                aria-label="Urgency reason"
                autoComplete="off"
                rows={3}
                className="mt-1"
                placeholder="e.g. Client has an SLA deadline tomorrow…"
              />
            </div>
            {error ? (
              <p className="text-sm text-red-600 dark:text-red-400" role="alert">
                {error}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel urgency edit"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Save urgency edit"
              disabled={!reason.trim() || mutation.isPending}
              onClick={handleSave}
            >
              {mutation.isPending ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
