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
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { REJECT_REASON_CODES, REJECT_REASON_LABELS, type RejectReasonCode } from "@/lib/routing"

const REJECT_ITEMS = [
  { label: "Select a reason", value: null },
  ...REJECT_REASON_CODES.map((code) => ({
    label: REJECT_REASON_LABELS[code],
    value: code,
  })),
]

type ApproveDraftDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  approveBody: string
  onApproveBodyChange: (value: string) => void
  approvalNote: string
  onApprovalNoteChange: (value: string) => void
  approvalScope: "once" | "similar" | ""
  onApprovalScopeChange: (value: "once" | "similar") => void
  busy: boolean
  isPending: boolean
  onConfirm: () => void
}

export const ApproveDraftDialog = ({
  open,
  onOpenChange,
  approveBody,
  onApproveBodyChange,
  approvalNote,
  onApprovalNoteChange,
  approvalScope,
  onApprovalScopeChange,
  busy,
  isPending,
  onConfirm,
}: ApproveDraftDialogProps) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent size="lg">
      <DialogHeader>
        <DialogTitle>Approve draft</DialogTitle>
        <DialogDescription>
          Review the reply body. Edit if needed, then approve. Email is never sent from this app.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4">
        <div>
          <p className="mb-1 text-xs text-gray-500">Draft body</p>
          <Textarea
            value={approveBody}
            onChange={(event) => onApproveBodyChange(event.target.value)}
            aria-label="Draft body to approve"
            name="approve_body"
            autoComplete="off"
            rows={14}
            className="min-h-[16rem]"
          />
        </div>
        <div className="space-y-3 border-t border-gray-200 pt-4 dark:border-gray-700">
          <div>
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100">Learning note</p>
            <p className="mt-0.5 text-xs text-gray-500">
              Optional. Add guidance only if you want to teach the system, for example after an
              edit, or a rule that should apply to similar emails later.
            </p>
          </div>
          <Textarea
            value={approvalNote}
            onChange={(event) => onApprovalNoteChange(event.target.value)}
            aria-label="Approval learning note"
            name="approval_note"
            autoComplete="off"
            rows={3}
            placeholder="e.g. Soften tone and lead with the invoice number…"
          />
          <fieldset className="space-y-2">
            <legend className="text-xs text-gray-500">
              If you add a note, where should it apply?
            </legend>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={approvalScope === "once" ? "default" : "outline"}
                tabIndex={0}
                aria-label="Apply learning to this thread only"
                aria-pressed={approvalScope === "once"}
                onClick={() => onApprovalScopeChange("once")}
              >
                Just this thread
              </Button>
              <Button
                type="button"
                size="sm"
                variant={approvalScope === "similar" ? "default" : "outline"}
                tabIndex={0}
                aria-label="Apply learning to similar emails"
                aria-pressed={approvalScope === "similar"}
                onClick={() => onApprovalScopeChange("similar")}
              >
                Similar emails in the future
              </Button>
            </div>
            {approvalNote.trim() && !approvalScope ? (
              <p className="text-xs text-amber-600 dark:text-amber-400" role="status">
                Choose a scope when providing a learning note.
              </p>
            ) : null}
          </fieldset>
        </div>
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          tabIndex={0}
          aria-label="Cancel approve"
          onClick={() => onOpenChange(false)}
        >
          Cancel
        </Button>
        <Button
          type="button"
          tabIndex={0}
          aria-label="Confirm approve draft"
          disabled={!approveBody.trim() || busy || (Boolean(approvalNote.trim()) && !approvalScope)}
          onClick={onConfirm}
        >
          {isPending ? "Approving…" : "Approve"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)

type RejectDraftDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  rejectNote: string
  onRejectNoteChange: (value: string) => void
  rejectReason: RejectReasonCode | ""
  onRejectReasonChange: (value: RejectReasonCode | "") => void
  busy: boolean
  isPending: boolean
  onConfirm: () => void
}

export const RejectDraftDialog = ({
  open,
  onOpenChange,
  rejectNote,
  onRejectNoteChange,
  rejectReason,
  onRejectReasonChange,
  busy,
  isPending,
  onConfirm,
}: RejectDraftDialogProps) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent size="lg">
      <DialogHeader>
        <DialogTitle>Reject draft</DialogTitle>
        <DialogDescription>
          Explain why this draft should be rejected. Email is never sent.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <div>
          <label className="text-xs text-gray-500" htmlFor="reject-reason">
            Why is this wrong?
          </label>
          <Select
            items={REJECT_ITEMS}
            value={rejectReason || null}
            onValueChange={(value) => onRejectReasonChange(value ?? "")}
          >
            <SelectTrigger id="reject-reason" aria-label="Rejection reason" className="mt-1 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {REJECT_ITEMS.map((item) => (
                  <SelectItem key={item.value ?? "placeholder"} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <p className="mt-1 text-xs text-gray-500">
            &ldquo;Wrong action / no reply needed&rdquo; marks this thread as no action and leaves
            Needs Attention. Any other reason will teach the system what to change next time.
          </p>
        </div>
        <Textarea
          value={rejectNote}
          onChange={(event) => onRejectNoteChange(event.target.value)}
          aria-label="Rejection note"
          name="reject_note"
          autoComplete="off"
          rows={6}
          placeholder="Why is this draft wrong…"
        />
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          tabIndex={0}
          aria-label="Cancel reject"
          onClick={() => onOpenChange(false)}
        >
          Cancel
        </Button>
        <Button
          type="button"
          variant="destructive"
          tabIndex={0}
          aria-label="Confirm reject draft"
          disabled={!rejectNote.trim() || !rejectReason || busy}
          onClick={onConfirm}
        >
          {isPending ? "Rejecting…" : "Reject"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)

type ResolvePromptDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  isPending: boolean
  onConfirm: () => void
}

export const ResolvePromptDialog = ({
  open,
  onOpenChange,
  isPending,
  onConfirm,
}: ResolvePromptDialogProps) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Mark this thread resolved?</DialogTitle>
        <DialogDescription>
          Review is done. Mark resolved to leave Needs Attention, or keep it open if you are still
          waiting on someone.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          tabIndex={0}
          aria-label="Keep thread open"
          onClick={() => onOpenChange(false)}
        >
          Keep open
        </Button>
        <Button
          type="button"
          tabIndex={0}
          aria-label="Mark thread resolved"
          disabled={isPending}
          onClick={onConfirm}
        >
          {isPending ? "Resolving…" : "Mark resolved"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)
