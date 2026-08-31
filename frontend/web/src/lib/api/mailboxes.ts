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
      stale_only?: boolean
      include_filtered?: boolean
      cursor?: string
      limit?: number
    } = {},
  ) {
    const qs = new URLSearchParams()
    if (params.state) qs.set("state", params.state)
    if (params.urgency) qs.set("urgency", params.urgency)
    if (params.stale_only) qs.set("stale_only", "true")
    if (params.include_filtered) qs.set("include_filtered", "true")
    if (params.cursor) qs.set("cursor", params.cursor)
    if (params.limit) qs.set("limit", String(params.limit))
    const query = qs.toString()
    return apiFetch<ThreadList>(
      `/api/mailboxes/${encodeURIComponent(mailbox)}/threads${query ? `?${query}` : ""}`,
    )
  },
}
