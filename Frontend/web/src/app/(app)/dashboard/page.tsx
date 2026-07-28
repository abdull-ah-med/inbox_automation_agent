"use client"

import { useQuery } from "@tanstack/react-query"

import { AttentionQueue } from "@/components/attention-queue"
import { Breadcrumbs } from "@/components/breadcrumbs"
import { MailboxSummaryCard } from "@/components/mailbox-summary-card"
import { PageTransition } from "@/components/motion"
import { UrgencyDistribution } from "@/components/urgency-distribution"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"

export default function DashboardPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["dashboard", "overview"],
    queryFn: () => api.dashboard.overview(),
  })

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-48 w-full rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <p className="text-sm text-red-600">
        {error instanceof Error ? error.message : "Failed to load dashboard"}
      </p>
    )
  }

  const totalFiltered = data.mailboxes.reduce(
    (sum, mailbox) => sum + mailbox.filtered_count,
    0,
  )

  return (
    <PageTransition>
      <Breadcrumbs items={[{ label: "Overview" }]} />
      <div className="mb-6">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Overview
        </h2>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {data.total_awaiting} awaiting action · {data.total_stale} stale
          {totalFiltered > 0
            ? ` · ${totalFiltered} filtered as spam/no action (hidden by default)`
            : ""}
        </p>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {data.mailboxes.map((mailbox, index) => (
          <MailboxSummaryCard
            key={mailbox.mailbox}
            mailbox={mailbox}
            index={index}
          />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <section className="rounded-lg border border-gray-200 bg-white p-5 lg:col-span-3 dark:border-gray-700 dark:bg-gray-900">
          <h3 className="mb-1 text-sm font-semibold text-gray-700 dark:text-gray-300">
            Needs attention
          </h3>
          <p className="mb-4 text-xs text-gray-500">
            Awaiting-action threads with urgency, spam, and context signals.
          </p>
          <AttentionQueue threads={data.needs_attention ?? []} />
        </section>
        <section className="rounded-lg border border-gray-200 bg-white p-5 lg:col-span-2 dark:border-gray-700 dark:bg-gray-900">
          <h3 className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-300">
            Urgency by inbox
          </h3>
          <UrgencyDistribution mailboxes={data.mailboxes} />
        </section>
      </div>
    </PageTransition>
  )
}
