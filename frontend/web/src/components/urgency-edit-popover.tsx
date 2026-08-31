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
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api-client"

const URGENCY_ITEMS = [
  { label: "CRITICAL", value: "CRITICAL" },
  { label: "HIGH", value: "HIGH" },
  { label: "NORMAL", value: "NORMAL" },
  { label: "LOW", value: "LOW" },
] as const

type UrgencyLevel = (typeof URGENCY_ITEMS)[number]["value"]

const isUrgencyLevel = (value: string | null): value is UrgencyLevel =>
  URGENCY_ITEMS.some((item) => item.value === value)

export const UrgencyEditPopover = ({
  draftId,
  threadId,
  currentUrgency,
  disabled = false,
  onSaved,
}: {
  draftId: string
  threadId: string
  currentUrgency: string | null
  disabled?: boolean
  onSaved?: (payload: { urgency: UrgencyLevel; reason: string }) => void
}) => {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [urgency, setUrgency] = useState<UrgencyLevel>(
    isUrgencyLevel(currentUrgency) ? currentUrgency : "NORMAL",
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
      const savedReason = reason.trim()
      const savedUrgency = urgency
      setOpen(false)
      setReason("")
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
      await queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] })
      await queryClient.invalidateQueries({ queryKey: ["mailbox"] })
      onSaved?.({ urgency: savedUrgency, reason: savedReason })
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const handleOpen = () => {
    setUrgency(isUrgencyLevel(currentUrgency) ? currentUrgency : "NORMAL")
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
              Change the urgency level and explain why. This teaches future drafts about off-thread
              or business context.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500" htmlFor="urgency-level">
                Urgency level
              </label>
              <Select
                items={URGENCY_ITEMS}
                value={urgency}
                onValueChange={(value) => {
                  if (!value) return
                  setUrgency(value)
                }}
              >
                <SelectTrigger
                  id="urgency-level"
                  aria-label="Urgency level"
                  className="mt-1 w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {URGENCY_ITEMS.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
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
