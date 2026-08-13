"use client"

import { useQuery } from "@tanstack/react-query"
import { useParams } from "next/navigation"

import { Breadcrumbs } from "@/components/breadcrumbs"
import { ErrorPage } from "@/components/error-page"
import { SentReplyPanel } from "@/components/sent-reply-panel"
import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { ThreadEmailPanel } from "@/components/thread-email-panel"
import { ThreadTriageSidebar } from "@/components/thread-triage-sidebar"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"
import { inboxColor, inboxLabel } from "@/lib/design-tokens"

export default function ThreadDetailPage() {
  const params = useParams<{ thread_id: string }>()
  const threadId = params.thread_id

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["thread", threadId],
    queryFn: () => api.threads.detail(threadId),
  })

  if (isLoading) {
    return (
      <div className="space-y-4" aria-busy="true" aria-live="polite">
        <Breadcrumbs
          items={[
            { label: "Overview", href: "/dashboard" },
            { label: "…" },
          ]}
        />
        <Skeleton className="h-16 w-full rounded-lg" />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
          <Skeleton className="h-[32rem] rounded-lg lg:col-span-2" />
          <Skeleton className="h-[32rem] rounded-lg lg:col-span-3" />
        </div>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="space-y-4">
        <Breadcrumbs
          items={[
            { label: "Overview", href: "/dashboard" },
            { label: "Thread" },
          ]}
        />
        <ErrorPage
          error={error}
          onRetry={() => {
            void refetch()
          }}
        />
      </div>
    )
  }

  const { thread, classification, draft, triage, messages, audit_log, sent_reply, draft_vs_sent_diff } =
    data
  const color = inboxColor(thread.mailbox_key)
  const label = inboxLabel(thread.mailbox_key)
  const urgency = classification?.urgency || draft?.urgency || thread.urgency
  const subject = thread.subject || "(no subject)"
  const showSentReply =
    thread.state === "RESOLVED" && sent_reply != null

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Overview", href: "/dashboard" },
          {
            label,
            href: `/mailboxes/${encodeURIComponent(thread.mailbox_key)}`,
          },
          { label: subject },
        ]}
      />

      <Card className="mb-5">
        <CardHeader className="gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="rounded-full px-2 py-0.5 text-xs font-medium text-white"
              style={{ backgroundColor: color }}
            >
              {label}
            </span>
            <StatusBadge label={stateLabel(thread.state)} tone={stateTone(thread.state)} />
            {urgency ? (
              <StatusBadge label={urgency} tone={urgencyTone(urgency)} />
            ) : null}
            {thread.category ? (
              <StatusBadge label={thread.category} tone="purple" />
            ) : null}
            {triage?.is_spam ? <StatusBadge label="Spam" tone="red" /> : null}
            {triage?.needs_context ? (
              <StatusBadge label="Needs context" tone="amber" />
            ) : null}
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-lg font-semibold text-card-foreground">
                {subject}
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {thread.last_sender ?? "Unknown sender"} · {thread.message_count}{" "}
                messages · {thread.mailbox}
              </p>
            </div>
            {thread.outlook_url ? (
              <a
                href={thread.outlook_url}
                target="_blank"
                rel="noopener noreferrer"
                tabIndex={0}
                aria-label="Open thread in Outlook"
                className="cursor-pointer text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
              >
                Open in Outlook
              </a>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
        <aside className="lg:col-span-2">
          <ThreadTriageSidebar
            threadId={threadId}
            thread={thread}
            classification={classification}
            draft={draft}
            triage={triage}
            auditLog={audit_log}
          />
        </aside>
        <div className="lg:col-span-3">
          {showSentReply && sent_reply ? (
            <SentReplyPanel
              sentReply={sent_reply}
              draft={draft}
              diff={draft_vs_sent_diff}
            />
          ) : null}
          <ThreadEmailPanel
            subject={thread.subject}
            messages={messages}
            outlookUrl={thread.outlook_url}
          />
        </div>
      </div>
    </>
  )
}
