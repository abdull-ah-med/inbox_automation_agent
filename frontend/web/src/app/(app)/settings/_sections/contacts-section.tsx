"use client"

import { useQuery } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { useMemo, useState } from "react"

import { ContactFormDialog } from "@/components/contact-form-dialog"
import { ContactsTable } from "@/app/(app)/settings/_sections/contacts-table"
import { DeleteContactDialog } from "@/app/(app)/settings/_sections/delete-contact-dialog"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  CONTACTS_PAGE_SIZE,
  useDeleteContact,
  useMailboxContacts,
} from "@/hooks/use-mailbox-contacts"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { api } from "@/lib/api-client"
import type { MailboxContactView, MailboxOverview } from "@/lib/types"

const CONTACTS_SEARCH_DEBOUNCE_MS = 200

type ContactsPanelProps = {
  mailboxes: MailboxOverview[]
  selectedKey: string
  selectedLabel: string
  onMailboxChange: (value: string | null) => void
}

const ContactsPanel = ({
  mailboxes,
  selectedKey,
  selectedLabel,
  onMailboxChange,
}: ContactsPanelProps) => {
  const [search, setSearch] = useState("")
  const debouncedSearch = useDebouncedValue(search, CONTACTS_SEARCH_DEBOUNCE_MS)
  const [offset, setOffset] = useState(0)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<MailboxContactView | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<MailboxContactView | null>(null)

  const { data, isLoading } = useMailboxContacts(selectedKey, {
    q: debouncedSearch,
    offset,
    limit: CONTACTS_PAGE_SIZE,
  })
  const deleteMutation = useDeleteContact(selectedKey)

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const showInitialSkeleton = isLoading && data == null
  const emptyMessage = useMemo(() => {
    if (debouncedSearch.trim()) return "No contacts match this filter."
    return "No saved contacts yet. Names you set on a draft appear here automatically."
  }, [debouncedSearch])

  const mailboxItems = useMemo(
    () => mailboxes.map((row) => ({ label: row.label, value: row.mailbox })),
    [mailboxes],
  )

  const handleSearchChange = (value: string) => {
    setSearch(value)
    setOffset(0)
  }

  const handleMailboxChange = (value: string | null) => {
    onMailboxChange(value)
    setOffset(0)
  }

  const handleOpenAdd = () => {
    setEditing(null)
    setDialogOpen(true)
  }

  const handleConfirmDelete = () => {
    if (!deleteTarget) return
    deleteMutation.mutate(deleteTarget.email, {
      onSuccess: () => setDeleteTarget(null),
    })
  }

  return (
    <Card className="mb-8" aria-labelledby="contacts-heading">
      <CardHeader>
        <CardTitle id="contacts-heading" className="text-lg">
          Contacts
        </CardTitle>
        <CardDescription>
          Greeting names for {selectedLabel}. Aliases override guessed names from email addresses,
          and updates on a draft apply instantly to that reply.
        </CardDescription>
        <CardAction>
          <Button
            type="button"
            size="sm"
            tabIndex={0}
            aria-label="Add contact"
            onClick={handleOpenAdd}
          >
            <Plus aria-hidden="true" />
            Add contact
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          {mailboxes.length > 1 ? (
            <Select items={mailboxItems} value={selectedKey} onValueChange={handleMailboxChange}>
              <SelectTrigger className="w-[220px]" aria-label="Mailbox for contacts" tabIndex={0}>
                <SelectValue placeholder="Select mailbox" />
              </SelectTrigger>
              <SelectContent>
                {mailboxItems.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
          <Input
            type="search"
            value={search}
            onChange={(event) => handleSearchChange(event.target.value)}
            placeholder="Filter by name or email"
            aria-label="Filter by name or email"
            tabIndex={0}
            className="max-w-sm flex-1"
          />
        </div>

        <div className="ring-foreground/10 overflow-hidden rounded-lg ring-1">
          {showInitialSkeleton ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : items.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">{emptyMessage}</p>
          ) : (
            <ContactsTable
              items={items}
              onEdit={(contact) => {
                setEditing(contact)
                setDialogOpen(true)
              }}
              onDelete={setDeleteTarget}
            />
          )}

          {total > CONTACTS_PAGE_SIZE ? (
            <div className="flex items-center justify-between border-t border-gray-100 px-4 py-3 dark:border-gray-800">
              <p className="text-muted-foreground text-xs">
                Showing {offset + 1}–{Math.min(offset + CONTACTS_PAGE_SIZE, total)} of {total}
              </p>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  tabIndex={0}
                  aria-label="Previous contacts page"
                  disabled={offset <= 0}
                  onClick={() => setOffset((value) => Math.max(0, value - CONTACTS_PAGE_SIZE))}
                >
                  Prev
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  tabIndex={0}
                  aria-label="Next contacts page"
                  disabled={offset + CONTACTS_PAGE_SIZE >= total}
                  onClick={() => setOffset((value) => value + CONTACTS_PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </CardContent>

      <ContactFormDialog
        mode={editing ? "edit" : "create"}
        mailbox={selectedKey}
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) setEditing(null)
        }}
        initialEmail={editing?.email ?? ""}
        initialFirstName={editing?.first_name ?? ""}
        initialFullName={editing?.full_name ?? ""}
        initialNotes={editing?.notes ?? null}
      />

      <DeleteContactDialog
        target={deleteTarget}
        busy={deleteMutation.isPending}
        onClear={() => setDeleteTarget(null)}
        onConfirm={handleConfirmDelete}
      />
    </Card>
  )
}

export const ContactsSection = () => {
  const { data: mailboxes = [] } = useQuery({
    queryKey: ["mailboxes"],
    queryFn: () => api.mailboxes.list(),
  })
  const [mailboxKey, setMailboxKey] = useState("")
  const selectedKey = mailboxKey || mailboxes[0]?.mailbox || ""
  const selectedLabel = mailboxes.find((row) => row.mailbox === selectedKey)?.label ?? selectedKey

  if (!selectedKey) {
    return (
      <Card className="mb-8" aria-labelledby="contacts-heading">
        <CardHeader>
          <CardTitle id="contacts-heading" className="text-lg">
            Contacts
          </CardTitle>
          <CardDescription>No mailboxes available yet.</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  return (
    <ContactsPanel
      mailboxes={mailboxes}
      selectedKey={selectedKey}
      selectedLabel={selectedLabel}
      onMailboxChange={(value) => {
        if (value) setMailboxKey(value)
      }}
    />
  )
}
