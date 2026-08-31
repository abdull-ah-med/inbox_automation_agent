"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api-client"

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type ContactFormDialogProps = {
  mode: "create" | "edit"
  mailbox: string
  open: boolean
  onOpenChange: (open: boolean) => void
  initialEmail?: string
  initialFirstName?: string
  initialFullName?: string
  initialNotes?: string | null
  /** When true, email field is not editable (chip always locks email). */
  emailLocked?: boolean
  /** When set, save rewrites this draft's greeting in place (no LLM). */
  draftId?: string
  onSaved?: () => void
}

type ContactFormFieldsProps = {
  mode: "create" | "edit"
  mailbox: string
  draftId?: string
  initialEmail: string
  initialFirstName: string
  initialFullName: string
  initialNotes: string
  lockEmail: boolean
  onOpenChange: (open: boolean) => void
  onSaved?: () => void
}

const ContactFormFields = ({
  mode,
  mailbox,
  draftId,
  initialEmail,
  initialFirstName,
  initialFullName,
  initialNotes,
  lockEmail,
  onOpenChange,
  onSaved,
}: ContactFormFieldsProps) => {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState(initialEmail)
  const [firstName, setFirstName] = useState(initialFirstName)
  const [fullName, setFullName] = useState(initialFullName)
  const [notes, setNotes] = useState(initialNotes)
  const [error, setError] = useState<string | null>(null)

  const emailOk = EMAIL_RE.test(email.trim())
  const firstOk = firstName.trim().length > 0
  const canSave = emailOk && firstOk

  const mutation = useMutation({
    mutationFn: async () => {
      const trimmedEmail = email.trim().toLowerCase()
      const trimmedFirst = firstName.trim()
      const trimmedFull = fullName.trim()
      const trimmedNotes = notes.trim() || null
      if (draftId) {
        return api.drafts.applySalutation(draftId, {
          email: trimmedEmail,
          first_name: trimmedFirst,
          full_name: trimmedFull,
          notes: trimmedNotes,
        })
      }
      if (mode === "edit") {
        return api.mailboxContacts.update(mailbox, {
          email: trimmedEmail,
          first_name: trimmedFirst,
          full_name: trimmedFull,
          notes: trimmedNotes,
        })
      }
      return api.mailboxContacts.upsert(mailbox, {
        email: trimmedEmail,
        first_name: trimmedFirst,
        full_name: trimmedFull,
        notes: trimmedNotes,
      })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["mailbox", mailbox, "contacts"] })
      await queryClient.invalidateQueries({ queryKey: ["thread"] })
      setError(null)
      onOpenChange(false)
      onSaved?.()
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const handleSave = () => {
    if (!canSave || mutation.isPending) return
    mutation.mutate()
  }

  const handleCancel = () => {
    onOpenChange(false)
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{mode === "edit" ? "Edit contact" : "Add contact"}</DialogTitle>
        <DialogDescription>
          {mode === "edit"
            ? "Update the name used in draft greetings for this address."
            : "Save a name to use in draft greetings for this address."}
          {draftId
            ? " The current draft greeting updates immediately — no regenerate needed."
            : null}
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="contact-email">Email</Label>
          <Input
            id="contact-email"
            type="email"
            value={email}
            readOnly={lockEmail}
            aria-readonly={lockEmail}
            aria-label="Contact email"
            tabIndex={0}
            onChange={(event) => setEmail(event.target.value)}
            className={lockEmail ? "bg-muted/40" : undefined}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="contact-first-name">First name</Label>
          <Input
            id="contact-first-name"
            value={firstName}
            autoFocus
            aria-label="Contact first name"
            tabIndex={0}
            onChange={(event) => setFirstName(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="contact-full-name">Full name</Label>
          <Input
            id="contact-full-name"
            value={fullName}
            aria-label="Contact full name"
            tabIndex={0}
            onChange={(event) => setFullName(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="contact-notes">Notes</Label>
          <Textarea
            id="contact-notes"
            value={notes}
            aria-label="Contact notes"
            tabIndex={0}
            onChange={(event) => setNotes(event.target.value)}
            rows={3}
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
          aria-label="Cancel contact edit"
          onClick={handleCancel}
        >
          Cancel
        </Button>
        <Button
          type="button"
          tabIndex={0}
          aria-label="Save contact"
          disabled={!canSave || mutation.isPending}
          onClick={handleSave}
        >
          {mutation.isPending ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </>
  )
}

const resolveInitial = (provided: string, stored: string | null | undefined): string =>
  provided || stored || ""

export const ContactFormDialog = ({
  mode,
  mailbox,
  open,
  onOpenChange,
  initialEmail = "",
  initialFirstName = "",
  initialFullName = "",
  initialNotes = null,
  emailLocked,
  draftId,
  onSaved,
}: ContactFormDialogProps) => {
  const lockEmail = emailLocked ?? mode === "edit"
  const emailKey = initialEmail.trim().toLowerCase()
  const canLookup = open && EMAIL_RE.test(emailKey)

  const { data: existing } = useQuery({
    queryKey: ["mailbox", mailbox, "contacts", "by-email", emailKey],
    queryFn: () => api.mailboxContacts.getOne(mailbox, emailKey),
    enabled: canLookup,
    retry: false,
  })

  const resolvedFirstName = resolveInitial(initialFirstName, existing?.first_name)
  const resolvedFullName = resolveInitial(initialFullName, existing?.full_name)
  const resolvedNotes = resolveInitial(initialNotes ?? "", existing?.notes)
  const formKey = [
    mode,
    draftId ?? "",
    emailKey,
    resolvedFirstName,
    resolvedFullName,
    resolvedNotes,
  ].join("|")

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open ? (
          <ContactFormFields
            key={formKey}
            mode={mode}
            mailbox={mailbox}
            draftId={draftId}
            initialEmail={initialEmail}
            initialFirstName={resolvedFirstName}
            initialFullName={resolvedFullName}
            initialNotes={resolvedNotes}
            lockEmail={lockEmail}
            onOpenChange={onOpenChange}
            onSaved={onSaved}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
