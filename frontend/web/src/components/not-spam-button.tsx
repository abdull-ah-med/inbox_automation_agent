"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState, type KeyboardEvent } from "react"

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

export const NotSpamButton = ({
  threadId,
  sender,
  size = "default",
}: {
  threadId: string
  sender: string | null
  size?: "default" | "xs" | "sm"
}) => {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const senderLabel = sender?.trim() || "this sender"

  const mutation = useMutation({
    mutationFn: () => api.threads.markNotSpam(threadId),
    onSuccess: async () => {
      setOpen(false)
      setActionError(null)
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
      await queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] })
      await queryClient.invalidateQueries({ queryKey: ["mailbox"] })
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const handleOpen = () => {
    setActionError(null)
    setOpen(true)
  }

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) setActionError(null)
  }

  const handleConfirm = () => {
    mutation.mutate()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleOpen()
    }
  }

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size={size}
        tabIndex={0}
        aria-label="Mark as not spam"
        onClick={handleOpen}
        onKeyDown={handleKeyDown}
      >
        Not spam
      </Button>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Mark as not spam</DialogTitle>
            <DialogDescription>
              Future mail from {senderLabel} will not be treated as spam. This app
              cannot move mail in Outlook — if the message is in Junk, it stays
              there.
            </DialogDescription>
          </DialogHeader>
          {actionError ? (
            <p className="text-sm text-destructive" role="alert">
              {actionError}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel not spam"
              onClick={() => handleOpenChange(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Confirm not spam"
              onClick={handleConfirm}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? "Working…" : "Confirm not spam"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
