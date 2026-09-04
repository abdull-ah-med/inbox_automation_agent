"use client"

import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { api } from "@/lib/api-client"

export const useMailboxFilter = () => {
  const query = useQuery({
    queryKey: ["mailboxes"],
    queryFn: () => api.mailboxes.list(),
  })
  const addresses = (query.data ?? []).map((row) => row.mailbox)
  const [selected, setSelected] = useState("")
  const mailbox = selected || addresses[0] || ""
  return { mailbox, addresses, setMailbox: setSelected, isLoading: query.isLoading }
}

export const MailboxFilter = ({
  mailbox,
  addresses,
  onMailboxChange,
}: {
  mailbox: string
  addresses: string[]
  onMailboxChange: (value: string) => void
}) => {
  const handleChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    onMailboxChange(event.target.value)
  }

  if (addresses.length === 0) return null

  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">Mailbox</span>
      <select
        aria-label="Mailbox"
        className="border-input bg-background rounded-md border px-2 py-1 text-sm"
        value={mailbox}
        onChange={handleChange}
        tabIndex={0}
      >
        {addresses.map((address) => (
          <option key={address} value={address}>
            {address}
          </option>
        ))}
      </select>
    </label>
  )
}
