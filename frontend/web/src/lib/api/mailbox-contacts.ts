import { apiFetch } from "@/lib/api/client"
import type {
  MailboxContactListResponse,
  MailboxContactPatch,
  MailboxContactUpsert,
  MailboxContactView,
} from "@/lib/types"

export const mailboxContactsApi = {
  list(mailbox: string, params: { q?: string; limit?: number; offset?: number } = {}) {
    const qs = new URLSearchParams()
    if (params.q) qs.set("q", params.q)
    if (params.limit != null) qs.set("limit", String(params.limit))
    if (params.offset != null) qs.set("offset", String(params.offset))
    const query = qs.toString()
    return apiFetch<MailboxContactListResponse>(
      `/api/mailboxes/${encodeURIComponent(mailbox)}/contacts${query ? `?${query}` : ""}`,
    )
  },
  getOne(mailbox: string, email: string) {
    const qs = new URLSearchParams({ email })
    return apiFetch<MailboxContactView>(
      `/api/mailboxes/${encodeURIComponent(mailbox)}/contacts/by-email?${qs}`,
    )
  },
  upsert(mailbox: string, body: MailboxContactUpsert) {
    return apiFetch<MailboxContactView>(`/api/mailboxes/${encodeURIComponent(mailbox)}/contacts`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  },
  update(mailbox: string, body: MailboxContactPatch) {
    return apiFetch<MailboxContactView>(
      `/api/mailboxes/${encodeURIComponent(mailbox)}/contacts/by-email`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    )
  },
  remove(mailbox: string, email: string) {
    const qs = new URLSearchParams({ email })
    return apiFetch<void>(`/api/mailboxes/${encodeURIComponent(mailbox)}/contacts/by-email?${qs}`, {
      method: "DELETE",
    })
  },
}
