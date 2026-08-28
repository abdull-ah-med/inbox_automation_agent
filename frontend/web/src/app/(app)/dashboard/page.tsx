"use client"

import { useQuery } from "@tanstack/react-query"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback } from "react"

import { AttentionQueue } from "@/components/attention-queue"
import { ErrorPage } from "@/components/error-page"
import { MailboxSummaryCard } from "@/components/mailbox-summary-card"
import { OpsReportDownload } from "@/components/ops-report-download"
import { UrgencyDistribution } from "@/components/urgency-distribution"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"
import type { NeedsAttentionSort } from "@/lib/types"

const ATTENTION_SORT_ITEMS = [
  { label: "Highest urgency", value: "urgency" as const },
  { label: "Most recent", value: "recent" as const },
]

const parseAttentionSort = (value: string | null): NeedsAttentionSort =>
  value === "recent" ? "recent" : "urgency"

export default function DashboardPage() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const attentionSort = parseAttentionSort(searchParams.get("attention_sort"))

  const handleAttentionSortChange = useCallback(
    (value: NeedsAttentionSort | null) => {
      const next = new URLSearchParams(searchParams.toString())
      if (!value || value === "urgency") next.delete("attention_sort")
      else next.set("attention_sort", value)
      const query = next.toString()
      router.replace(query ? `${pathname}?${query}` : pathname)
    },
    [pathname, router, searchParams],
  )

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["dashboard", "overview", attentionSort],
    queryFn: () => api.dashboard.overview(attentionSort),
  })

  if (isLoading) {
    return (
      <div className="space-y-6" aria-busy="true" aria-live="polite">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-48 w-full rounded-xl" />
          ))}
        </div>
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <ErrorPage
        error={error}
        onRetry={() => {
          void refetch()
        }}
      />
    )
  }

  const totalFiltered = data.mailboxes.reduce((sum, mailbox) => sum + mailbox.filtered_count, 0)

  return (
    <>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Overview</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {data.total_awaiting} awaiting action · {data.total_stale} stale
            {totalFiltered > 0
              ? ` · ${totalFiltered} filtered as spam/no action (hidden by default)`
              : ""}
          </p>
        </div>
        <OpsReportDownload />
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {data.mailboxes.map((mailbox) => (
          <MailboxSummaryCard key={mailbox.mailbox} mailbox={mailbox} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0">
            <div className="min-w-0 space-y-1">
              <CardTitle className="text-sm">Needs attention</CardTitle>
              <CardDescription>
                Awaiting-action threads with urgency, spam, and context signals.
              </CardDescription>
            </div>
            <Select value={attentionSort} onValueChange={handleAttentionSortChange}>
              <SelectTrigger
                size="sm"
                className="w-[11.5rem] shrink-0"
                aria-label="Sort needs attention queue"
              >
                <SelectValue placeholder="Sort by" />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectGroup>
                  {ATTENTION_SORT_ITEMS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            <AttentionQueue threads={data.needs_attention ?? []} />
          </CardContent>
        </Card>
        <Card className="h-fit self-start lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm">Urgency by inbox</CardTitle>
          </CardHeader>
          <CardContent>
            <UrgencyDistribution mailboxes={data.mailboxes} />
          </CardContent>
        </Card>
      </div>
    </>
  )
}
