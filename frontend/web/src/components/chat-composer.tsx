"use client"

import type {
  ChangeEvent,
  FormEvent,
  KeyboardEvent,
  RefObject,
} from "react"
import { ArrowUpIcon, SquareIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

export type ChatComposerMailboxItem = {
  label: string
  value: string | null
}

type ChatComposerProps = {
  message: string
  onMessageChange: (event: ChangeEvent<HTMLTextAreaElement>) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void
  isAsking: boolean
  validation: string | null
  onStop: () => void
  mailbox: string
  mailboxItems: ChatComposerMailboxItem[]
  onMailboxChange: (value: string | null) => void
  mailboxesLoading: boolean
  inputRef: RefObject<HTMLTextAreaElement | null>
  composerRows: number
}

export const ChatComposer = ({
  message,
  onMessageChange,
  onSubmit,
  onKeyDown,
  isAsking,
  validation,
  onStop,
  mailbox,
  mailboxItems,
  onMailboxChange,
  mailboxesLoading,
  inputRef,
  composerRows,
}: ChatComposerProps) => {
  const handleStop = () => {
    onStop()
  }

  return (
    <footer className="shrink-0 space-y-2 border-t border-border p-3">
      <Select
        items={mailboxItems}
        value={mailbox || null}
        onValueChange={onMailboxChange}
        disabled={isAsking || mailboxesLoading}
      >
        <SelectTrigger
          aria-label="Mailbox"
          size="sm"
          className="h-8 w-full"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent side="top" align="start">
          <SelectGroup>
            {mailboxItems.map((item) => (
              <SelectItem
                key={item.value ?? "all"}
                value={item.value}
              >
                {item.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <form onSubmit={onSubmit}>
        <div
          className={cn(
            "flex min-w-0 items-end gap-2 rounded-2xl border border-border bg-muted/60 px-3 py-2",
            "transition-[box-shadow,border-color] focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/20",
          )}
        >
          <Textarea
            ref={inputRef}
            id="inboxassistant-input"
            name="message"
            value={message}
            onChange={onMessageChange}
            onKeyDown={onKeyDown}
            placeholder="Ask about a thread…"
            aria-label="Message InboxAssistant"
            aria-invalid={validation ? true : undefined}
            aria-describedby={validation ? "inboxassistant-validation" : undefined}
            rows={composerRows}
            className="field-sizing-content max-h-40 min-h-8 flex-1 resize-none overflow-y-auto wrap-anywhere border-0 bg-transparent px-0 py-1.5 leading-6 shadow-none focus-visible:ring-0 dark:bg-transparent"
          />
          {isAsking ? (
            <Button
              type="button"
              size="icon-sm"
              aria-label="Stop generating"
              onClick={handleStop}
              className="mb-0.5 size-8 shrink-0 rounded-full"
            >
              <SquareIcon className="size-3.5 fill-current" aria-hidden="true" />
            </Button>
          ) : null}
          <Button
            type="submit"
            size="icon-sm"
            aria-label="Send"
            disabled={isAsking}
            tabIndex={isAsking ? -1 : 0}
            aria-hidden={isAsking}
            className={cn(
              "mb-0.5 size-8 shrink-0 rounded-full",
              isAsking && "hidden",
            )}
          >
            <ArrowUpIcon className="size-4" aria-hidden="true" />
          </Button>
        </div>
      </form>

      {validation ? (
        <p
          id="inboxassistant-validation"
          className="text-sm text-red-600 dark:text-red-400"
          role="alert"
        >
          {validation}
        </p>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          <span className="mb-0.5 hidden items-center gap-1.5 sm:inline-flex">
            <Kbd>⌘</Kbd>
            <Kbd>↵</Kbd>
            <span>to send.</span>
          </span>
          <span className="sm:ml-1">InboxAssistant never sends mail.</span>
        </p>
      )}
    </footer>
  )
}
