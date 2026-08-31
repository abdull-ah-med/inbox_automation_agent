"use client"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { MailboxContactView } from "@/lib/types"

type DeleteContactDialogProps = {
  target: MailboxContactView | null
  busy: boolean
  onClear: () => void
  onConfirm: () => void
}

export const DeleteContactDialog = ({
  target,
  busy,
  onClear,
  onConfirm,
}: DeleteContactDialogProps) => (
  <Dialog
    open={target != null}
    onOpenChange={(open) => {
      if (!open) onClear()
    }}
  >
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Remove contact</DialogTitle>
        <DialogDescription>
          Remove alias for {target?.email}? Drafts will fall back to the guessed name.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          tabIndex={0}
          aria-label="Cancel delete contact"
          onClick={onClear}
        >
          Cancel
        </Button>
        <Button
          type="button"
          variant="destructive"
          tabIndex={0}
          aria-label="Confirm delete contact"
          disabled={busy || !target}
          onClick={onConfirm}
        >
          {busy ? "Removing…" : "Remove"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)
