"use client"

import { useQuery } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api } from "@/lib/api-client"
import { inboxLabel } from "@/lib/design-tokens"
import { MAILBOXES_LIST_QUERY_KEY } from "@/lib/query-keys"
import type { MailboxOverview } from "@/lib/types"

const mailboxSelectLabel = (row: MailboxOverview) =>
  inboxLabel(row.mailbox, row.label || row.email_address)

export const useMailboxFilter = () => {
  const query = useQuery({
    queryKey: MAILBOXES_LIST_QUERY_KEY,
    queryFn: () => api.mailboxes.list(),
  })
  const mailboxes = query.data ?? []
  const [selected, setSelected] = useState("")
  const mailbox = selected || mailboxes[0]?.mailbox || ""
  return { mailbox, mailboxes, setMailbox: setSelected, isLoading: query.isLoading }
}

export const MailboxFilter = ({
  mailbox,
  mailboxes,
  onMailboxChange,
}: {
  mailbox: string
  mailboxes: MailboxOverview[]
  onMailboxChange: (value: string) => void
}) => {
  const items = useMemo(
    () =>
      mailboxes.map((row) => ({
        value: row.mailbox,
        label: mailboxSelectLabel(row),
      })),
    [mailboxes],
  )

  const handleValueChange = (value: string | null) => {
    if (!value) return
    onMailboxChange(value)
  }

  if (items.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-muted-foreground text-sm">Mailbox</span>
      <Select items={items} value={mailbox} onValueChange={handleValueChange}>
        <SelectTrigger aria-label="Mailbox" size="sm" className="min-h-10 min-w-[10rem]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {items.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  )
}
