"use client"

import { Pencil, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { formatRelativeTime } from "@/lib/dates"
import type { MailboxContactView } from "@/lib/types"

type ContactsTableProps = {
  items: MailboxContactView[]
  onEdit: (contact: MailboxContactView) => void
  onDelete: (contact: MailboxContactView) => void
}

const handleActivation = (event: React.KeyboardEvent<HTMLButtonElement>, action: () => void) => {
  if (event.key !== "Enter" && event.key !== " ") return
  event.preventDefault()
  action()
}

export const ContactsTable = ({ items, onEdit, onDelete }: ContactsTableProps) => (
  <div className="overflow-x-auto">
    <table className="w-full text-left text-sm">
      <thead className="bg-muted/40 text-muted-foreground text-xs">
        <tr>
          <th className="px-4 py-2 font-medium" scope="col">
            Email
          </th>
          <th className="px-4 py-2 font-medium" scope="col">
            First name
          </th>
          <th className="px-4 py-2 font-medium" scope="col">
            Full name
          </th>
          <th className="px-4 py-2 font-medium" scope="col">
            Updated
          </th>
          <th className="px-4 py-2 font-medium" scope="col">
            Actions
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
        {items.map((contact) => (
          <tr key={contact.email}>
            <td className="px-4 py-3 text-gray-900 dark:text-gray-100">{contact.email}</td>
            <td className="px-4 py-3 text-gray-900 dark:text-gray-100">{contact.first_name}</td>
            <td className="px-4 py-3 text-gray-700 dark:text-gray-300">
              {contact.full_name || "—"}
            </td>
            <td className="text-muted-foreground px-4 py-3 text-xs">
              {formatRelativeTime(contact.updated_at)}
            </td>
            <td className="px-4 py-3">
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  tabIndex={0}
                  aria-label={`Edit contact ${contact.email}`}
                  onClick={() => onEdit(contact)}
                  onKeyDown={(event) => handleActivation(event, () => onEdit(contact))}
                >
                  <Pencil aria-hidden="true" />
                  Edit
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  tabIndex={0}
                  aria-label={`Delete contact ${contact.email}`}
                  onClick={() => onDelete(contact)}
                  onKeyDown={(event) => handleActivation(event, () => onDelete(contact))}
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
)
