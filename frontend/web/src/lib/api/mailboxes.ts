import { apiFetch } from "@/lib/api/client"
import type { MailboxOverview, ThreadList } from "@/lib/types"

export const mailboxesApi = {
  list() {
    return apiFetch<MailboxOverview[]>("/api/mailboxes")
  },
  threads(
    mailbox: string,
    params: {
      state?: string
      urgency?: string
      from?: string
      to?: string
      cursor?: string
      limit?: number
    } = {},
  ) {
    const qs = new URLSearchParams()
    if (params.state) qs.set("state", params.state)
    if (params.urgency) qs.set("urgency", params.urgency)
    if (params.from) qs.set("from", params.from)
    if (params.to) qs.set("to", params.to)
    if (params.cursor) qs.set("cursor", params.cursor)
    if (params.limit) qs.set("limit", String(params.limit))
    const query = qs.toString()
    return apiFetch<ThreadList>(
      `/api/mailboxes/${encodeURIComponent(mailbox)}/threads${query ? `?${query}` : ""}`,
    )
  },
}
