"use client"

import { useState } from "react"

import { ContactFormDialog } from "@/components/contact-form-dialog"
import type { ReplyAddresseeView } from "@/lib/types"

type ChipPresentation = {
  label: string
  title: string
  muted: boolean
  showSavedDot: boolean
  disabled: boolean
  mode: "create" | "edit"
}

export const chipForAddressee = (addressee: ReplyAddresseeView): ChipPresentation => {
  if (addressee.source_kind === "team") {
    return {
      label: "team",
      title: "Role mailbox — no personal alias applied.",
      muted: false,
      showSavedDot: false,
      disabled: true,
      mode: "create",
    }
  }
  if (addressee.salute_name === "") {
    return {
      label: "— none —",
      title: "No name — click to add.",
      muted: true,
      showSavedDot: false,
      disabled: false,
      mode: "create",
    }
  }
  if (addressee.source_kind === "directory") {
    return {
      label: addressee.salute_name,
      title: "Saved alias — click to edit.",
      muted: false,
      showSavedDot: true,
      disabled: false,
      mode: "edit",
    }
  }
  if (addressee.source_kind === "local_part") {
    return {
      label: addressee.salute_name,
      title: "Guessed from email address — click to correct.",
      muted: true,
      showSavedDot: false,
      disabled: false,
      mode: "create",
    }
  }
  return {
    label: addressee.salute_name,
    title: "From the sender's own signature/display name — click to override.",
    muted: false,
    showSavedDot: false,
    disabled: false,
    mode: "create",
  }
}

type SaluteChipProps = {
  mailboxKey: string
  draftId: string
  replyAddressee: ReplyAddresseeView
}

export const SaluteChip = ({ mailboxKey, draftId, replyAddressee }: SaluteChipProps) => {
  const [dialogOpen, setDialogOpen] = useState(false)
  const chip = chipForAddressee(replyAddressee)

  const handleOpenChip = () => {
    if (chip.disabled) return
    setDialogOpen(true)
  }

  const handleChipKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return
    event.preventDefault()
    handleOpenChip()
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-muted-foreground text-xs">Salute</p>
        <button
          type="button"
          tabIndex={0}
          aria-label={`Salute: ${chip.label}`}
          title={chip.title}
          disabled={chip.disabled}
          onClick={handleOpenChip}
          onKeyDown={handleChipKeyDown}
          className={[
            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs ring-1 ring-foreground/10",
            chip.muted ? "bg-muted/60 text-muted-foreground" : "bg-muted/40 text-foreground",
            chip.disabled ? "cursor-not-allowed opacity-70" : "hover:bg-muted cursor-pointer",
          ].join(" ")}
        >
          <span>{chip.label}</span>
          {chip.showSavedDot ? (
            <span
              className="inline-block size-1.5 rounded-full bg-emerald-500"
              aria-hidden="true"
            />
          ) : null}
          {!chip.disabled ? <span aria-hidden="true">▾</span> : null}
        </button>
      </div>
      <ContactFormDialog
        mode={chip.mode}
        mailbox={mailboxKey}
        draftId={draftId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initialEmail={replyAddressee.email}
        initialFirstName={replyAddressee.salute_name}
        emailLocked
      />
    </div>
  )
}
