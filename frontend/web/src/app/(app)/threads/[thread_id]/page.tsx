"use client"

import { Suspense } from "react"

import { useQuery } from "@tanstack/react-query"
import { useParams, useSearchParams } from "next/navigation"

import { CourtesyCloseBanner } from "@/components/courtesy-close-banner"
import { AssociatedThreadsList } from "@/components/associated-threads-list"
import { Breadcrumbs } from "@/components/breadcrumbs"
import { ErrorPage } from "@/components/error-page"
import { PresentationBadges } from "@/components/presentation-badges"
import { RecurrenceBanner } from "@/components/recurrence-banner"
import { ResolutionBanner } from "@/components/resolution-banner"
import { SentReplyPanel } from "@/components/sent-reply-panel"
import { ThreadEmailPanel } from "@/components/thread-email-panel"
import { ThreadOriginBanner } from "@/components/thread-origin-banner"
import { ThreadTriageSidebar } from "@/components/thread-triage-sidebar"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"
import { inboxColor, inboxLabel } from "@/lib/design-tokens"
import { formatThreadHeaderMeta } from "@/lib/thread-header-meta"
import { cn, textLinkClass } from "@/lib/utils"

// `useSearchParams` opts the client-component tree up to the nearest
// Suspense boundary into CSR during prerendering (Next.js docs: "Missing
// Suspense boundary with useSearchParams"). Isolate it in a child so the
// route can still prerender a static shell instead of bailing whole-page.
function ThreadDetailPageSkeleton() {
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

export default function ThreadDetailPage() {
  return (
    <Suspense fallback={<ThreadDetailPageSkeleton />}>
      <ThreadDetailPageContent />
    </Suspense>
  )
}

function ThreadDetailPageContent() {
  const params = useParams<{ thread_id: string }>()
  const searchParams = useSearchParams()
  const threadId = params.thread_id
  const fromId = searchParams.get("from")
  const originId = fromId && fromId !== threadId ? fromId : null

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["thread", threadId],
    queryFn: () => api.threads.detail(threadId),
  })
  const originQuery = useQuery({
    queryKey: ["thread", originId],
    queryFn: () => api.threads.detail(originId ?? ""),
    enabled: originId != null,
  })

  if (isLoading) {
    return <ThreadDetailPageSkeleton />
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

  const { thread, classification, draft, triage, messages, audit_log, activity, sent_reply, draft_vs_sent_diff, associated_threads } =
    data
  const associatedItems = associated_threads ?? []
  const color = inboxColor(thread.mailbox_key)
  const label = inboxLabel(thread.mailbox_key)
  const presentation = thread.presentation
  const subject = thread.subject || "(no subject)"
  const showSentReply =
    thread.state === "RESOLVED" && sent_reply != null

  const lastInbound = [...messages].reverse().find((row) => row.direction === "inbound")
  const originSubject =
    originQuery.data?.thread.subject?.trim() || "the thread you were reviewing"
  const crumbItems = [
    { label: "Overview", href: "/dashboard" },
    {
      label,
      href: `/mailboxes/${encodeURIComponent(thread.mailbox_key)}`,
    },
    ...(originId
      ? [{ label: originSubject, href: `/threads/${originId}` }]
      : []),
    { label: subject },
  ]

  return (
    <>
      <Breadcrumbs items={crumbItems} />
      {originId ? (
        <ThreadOriginBanner originId={originId} originSubject={originSubject} />
      ) : null}

      <Card className="mb-5">
        <CardHeader className="gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="rounded-full px-2 py-0.5 text-xs font-medium text-white"
              style={{ backgroundColor: color }}
            >
              {label}
            </span>
            {presentation?.badges_now?.length ? (
              <PresentationBadges badges={presentation.badges_now} />
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
                {formatThreadHeaderMeta({
                  mailbox: thread.mailbox,
                  lastSender: thread.last_sender,
                  messageCount: thread.message_count,
                  messages,
                })}
              </p>
              {presentation && !presentation.urgency_active && presentation.urgency_assessed ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  Assessed urgency {presentation.urgency_assessed} (inactive — finished thread)
                </p>
              ) : null}
            </div>
            {thread.outlook_url ? (
              <a
                href={thread.outlook_url}
                target="_blank"
                rel="noopener noreferrer"
                tabIndex={0}
                aria-label="Open thread in Outlook"
                className={cn(textLinkClass, "text-sm font-medium")}
              >
                Open in Outlook
              </a>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <ResolutionBanner
        threadId={threadId}
        presentation={presentation}
        urgencyAssessed={presentation?.urgency_assessed ?? thread.urgency}
        activity={activity ?? []}
      />
      <RecurrenceBanner threadId={threadId} activity={activity ?? []} />
      <CourtesyCloseBanner
        state={thread.state}
        lastInboundBody={lastInbound?.body_text ?? null}
      />
      <AssociatedThreadsList
        sourceThreadId={threadId}
        sourceSubject={subject}
        items={associatedItems}
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
        <aside className="lg:col-span-2">
          <ThreadTriageSidebar
            threadId={threadId}
            thread={thread}
            classification={classification}
            draft={draft}
            triage={triage}
            auditLog={audit_log}
            activity={activity ?? []}
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
