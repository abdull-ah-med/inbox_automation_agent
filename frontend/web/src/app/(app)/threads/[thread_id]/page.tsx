"use client"

import { Suspense } from "react"

import { useQuery } from "@tanstack/react-query"
import { useParams, useSearchParams } from "next/navigation"

import { CourtesyCloseBanner } from "@/components/courtesy-close-banner"
import { AssociatedThreadsList } from "@/components/associated-threads-list"
import { Breadcrumbs } from "@/components/breadcrumbs"
import { ErrorPage } from "@/components/error-page"
import { RecurrenceBanner } from "@/components/recurrence-banner"
import { ResolutionBanner } from "@/components/resolution-banner"
import { SentReplyPanel } from "@/components/sent-reply-panel"
import { ThreadDetailHeader } from "@/components/thread-detail-header"
import { ThreadEmailPanel } from "@/components/thread-email-panel"
import { ThreadOriginBanner } from "@/components/thread-origin-banner"
import { ThreadTriageSidebar } from "@/components/thread-triage-sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"
import { inboxLabel } from "@/lib/design-tokens"

// `useSearchParams` opts the client-component tree up to the nearest
// Suspense boundary into CSR during prerendering (Next.js docs: "Missing
// Suspense boundary with useSearchParams"). Isolate it in a child so the
// route can still prerender a static shell instead of bailing whole-page.
function ThreadDetailPageSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-live="polite">
      <Breadcrumbs items={[{ label: "Overview", href: "/dashboard" }, { label: "…" }]} />
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
    queryKey: ["thread", originId, "header"],
    queryFn: () => api.threads.header(originId ?? ""),
    enabled: originId != null,
  })

  if (isLoading) {
    return <ThreadDetailPageSkeleton />
  }

  if (isError || !data) {
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: "Overview", href: "/dashboard" }, { label: "Thread" }]} />
        <ErrorPage
          error={error}
          onRetry={() => {
            void refetch()
          }}
        />
      </div>
    )
  }

  return (
    <ThreadDetailLoaded
      threadId={threadId}
      originId={originId}
      originSubject={originQuery.data?.subject?.trim() || "the thread you were reviewing"}
      data={data}
    />
  )
}

type ThreadDetailData = Awaited<ReturnType<typeof api.threads.detail>>

const ThreadDetailBanners = ({
  threadId,
  thread,
  presentation,
  messages,
  activity,
}: {
  threadId: string
  thread: ThreadDetailData["thread"]
  presentation: ThreadDetailData["thread"]["presentation"]
  messages: ThreadDetailData["messages"]
  activity: ThreadDetailData["activity"]
}) => {
  const lastInbound = messages.toReversed().find((row) => row.direction === "inbound")
  const hideCourtesyClose =
    Boolean(presentation?.show_resolution_banner) ||
    presentation?.disposition === "fyi_briefing" ||
    presentation?.disposition === "resolved_draftassistant" ||
    presentation?.disposition === "resolved_elise"

  return (
    <>
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
        hidden={hideCourtesyClose}
      />
    </>
  )
}

const ThreadDetailLoaded = ({
  threadId,
  originId,
  originSubject,
  data,
}: {
  threadId: string
  originId: string | null
  originSubject: string
  data: ThreadDetailData
}) => {
  const {
    thread,
    classification,
    draft,
    triage,
    messages,
    audit_log,
    activity,
    sent_reply,
    draft_vs_sent_diff,
    associated_threads,
    reply_addressee,
  } = data
  const associatedItems = associated_threads ?? []
  const label = inboxLabel(thread.mailbox_key)
  const presentation = thread.presentation
  const subject = thread.subject || "(no subject)"
  const showSentReply = thread.state === "RESOLVED" && sent_reply != null
  const crumbItems = [
    { label: "Overview", href: "/dashboard" },
    {
      label,
      href: `/mailboxes/${encodeURIComponent(thread.mailbox_key)}`,
    },
    ...(originId ? [{ label: originSubject, href: `/threads/${originId}` }] : []),
    { label: subject },
  ]

  return (
    <>
      <Breadcrumbs items={crumbItems} />
      {originId ? <ThreadOriginBanner originId={originId} originSubject={originSubject} /> : null}

      <ThreadDetailHeader thread={thread} messages={messages} />

      <ThreadDetailBanners
        threadId={threadId}
        thread={thread}
        presentation={presentation}
        messages={messages}
        activity={activity}
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
            replyAddressee={reply_addressee ?? null}
          />
        </aside>
        <div className="lg:col-span-3">
          {showSentReply && sent_reply ? (
            <SentReplyPanel sentReply={sent_reply} draft={draft} diff={draft_vs_sent_diff} />
          ) : null}
          <ThreadEmailPanel
            threadId={threadId}
            subject={thread.subject}
            messages={messages}
            outlookUrl={thread.outlook_url}
          />
        </div>
      </div>
    </>
  )
}
