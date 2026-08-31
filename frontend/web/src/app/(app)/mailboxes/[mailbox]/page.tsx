"use client"

import { Suspense, useCallback, useEffect, useMemo, useRef, type ChangeEvent } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation"

import { Breadcrumbs } from "@/components/breadcrumbs"
import { EmptyState } from "@/components/empty-state"
import { ErrorPage } from "@/components/error-page"
import { ThreadCard } from "@/components/thread-card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
import { inboxAccentStyle, inboxChipClassName, inboxLabel } from "@/lib/design-tokens"

const STATE_ITEMS = [
  { label: "All actionable states", value: null },
  { label: "New — not yet triaged", value: "NEW" },
  { label: "Awaiting client", value: "AWAITING_CLIENT" },
  { label: "Awaiting vendor", value: "AWAITING_VENDOR" },
  { label: "Awaiting partner", value: "AWAITING_PARTNER" },
  { label: "Draft ready", value: "DRAFTED" },
  { label: "Needs human review", value: "REQUIRES_HUMAN" },
  { label: "Resolved", value: "RESOLVED" },
  { label: "No action needed", value: "NO_ACTION" },
  { label: "Spam", value: "SPAM" },
] as const

const URGENCY_ITEMS = [
  { label: "All urgency", value: null },
  { label: "CRITICAL", value: "CRITICAL" },
  { label: "HIGH", value: "HIGH" },
  { label: "NORMAL", value: "NORMAL" },
  { label: "LOW", value: "LOW" },
] as const

const MailboxPageSkeleton = () => (
  <div className="space-y-4" aria-busy="true" aria-live="polite">
    <Breadcrumbs items={[{ label: "Overview", href: "/dashboard" }, { label: "…" }]} />
    <Skeleton className="h-10 w-48 rounded-lg" />
    <div className="space-y-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-24 w-full rounded-lg" />
      ))}
    </div>
  </div>
)

// `useSearchParams` opts the client-component tree up to the nearest
// Suspense boundary into CSR during prerendering (Next.js docs: "Missing
// Suspense boundary with useSearchParams"). Isolate it in a child so the
// route can still prerender a static shell instead of bailing whole-page.
export default function MailboxPage() {
  return (
    <Suspense fallback={<MailboxPageSkeleton />}>
      <MailboxPageContent />
    </Suspense>
  )
}

function MailboxPageContent() {
  const params = useParams<{ mailbox: string }>()
  const mailbox = decodeURIComponent(params.mailbox)
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  // Filters live in the URL so they're shareable and survive back/forward nav.
  const state = searchParams.get("state") ?? ""
  const urgency = searchParams.get("urgency") ?? ""
  const dateFrom = searchParams.get("from") ?? ""
  const dateTo = searchParams.get("to") ?? ""

  const updateFilters = useCallback(
    (patch: { state?: string; urgency?: string; from?: string; to?: string }) => {
      const next = new URLSearchParams(searchParams.toString())
      const nextState = patch.state ?? state
      const nextUrgency = patch.urgency ?? urgency
      const nextFrom = patch.from ?? dateFrom
      const nextTo = patch.to ?? dateTo

      if (nextState) next.set("state", nextState)
      else next.delete("state")
      if (nextUrgency) next.set("urgency", nextUrgency)
      else next.delete("urgency")
      if (nextFrom) next.set("from", nextFrom)
      else next.delete("from")
      if (nextTo) next.set("to", nextTo)
      else next.delete("to")
      // Drop legacy toggle params if present in a shared URL.
      next.delete("stale")
      next.delete("filtered")

      const query = next.toString()
      router.replace(query ? `${pathname}?${query}` : pathname)
    },
    [pathname, router, searchParams, state, urgency, dateFrom, dateTo],
  )

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    error,
    refetch,
  } = useInfiniteQuery({
    queryKey: ["mailbox", mailbox, "threads", state, urgency, dateFrom, dateTo],
    queryFn: ({ pageParam }) =>
      api.mailboxes.threads(mailbox, {
        state: state || undefined,
        urgency: urgency || undefined,
        from: dateFrom || undefined,
        to: dateTo || undefined,
        cursor: pageParam,
        limit: 25,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })

  const threads = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data])

  // TanStack Query infinite-scroll pattern: depend on stable query fields,
  // not the whole query object (recreated each render).
  // https://tanstack.com/query/latest/docs/framework/react/guides/infinite-queries
  useEffect(() => {
    const node = sentinelRef.current
    if (!node) return
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
        void fetchNextPage()
      }
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [fetchNextPage, hasNextPage, isFetchingNextPage])

  const label = inboxLabel(mailbox)
  const filtersActive = Boolean(state || urgency || dateFrom || dateTo)

  const handleDateFromChange = (event: ChangeEvent<HTMLInputElement>) => {
    updateFilters({ from: event.target.value })
  }

  const handleDateToChange = (event: ChangeEvent<HTMLInputElement>) => {
    updateFilters({ to: event.target.value })
  }

  const handleResetFilters = () => {
    router.replace(pathname)
  }

  return (
    <>
      <Breadcrumbs items={[{ label: "Overview", href: "/dashboard" }, { label }]} />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className={inboxChipClassName} style={inboxAccentStyle(mailbox)}>
          {label}
        </span>
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Threads</h2>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select
          items={STATE_ITEMS}
          value={state || null}
          onValueChange={(value) => updateFilters({ state: value ?? "" })}
        >
          <SelectTrigger aria-label="Filter by state" size="sm" className="min-h-10">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {STATE_ITEMS.map((item) => (
                <SelectItem key={item.value ?? "all"} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <Select
          items={URGENCY_ITEMS}
          value={urgency || null}
          onValueChange={(value) => updateFilters({ urgency: value ?? "" })}
        >
          <SelectTrigger aria-label="Filter by urgency" size="sm" className="min-h-10">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {URGENCY_ITEMS.map((item) => (
                <SelectItem key={item.value ?? "all"} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <label className="text-muted-foreground flex min-h-10 items-center gap-1.5 text-sm">
          <span className="sr-only">From date (UTC)</span>
          <span aria-hidden="true">From (UTC)</span>
          <Input
            type="date"
            aria-label="From date (UTC)"
            title="Inclusive start of day in UTC"
            value={dateFrom}
            className="w-auto min-w-38"
            onChange={handleDateFromChange}
          />
        </label>
        <label className="text-muted-foreground flex min-h-10 items-center gap-1.5 text-sm">
          <span className="sr-only">To date (UTC)</span>
          <span aria-hidden="true">To (UTC)</span>
          <Input
            type="date"
            aria-label="To date (UTC)"
            title="Inclusive end of day in UTC"
            value={dateTo}
            className="w-auto min-w-38"
            onChange={handleDateToChange}
          />
        </label>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="min-h-10"
          onClick={handleResetFilters}
        >
          Reset
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-2" aria-busy="true" aria-live="polite">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : isError ? (
        <ErrorPage
          error={error}
          onRetry={() => {
            void refetch()
          }}
        />
      ) : threads.length === 0 ? (
        <EmptyState
          title={filtersActive ? "No matching threads" : "No mail in this inbox"}
          description={
            filtersActive
              ? "No results for the current filters in this mailbox."
              : `${label} has no ingested threads yet. New mail will appear here after the poller runs.`
          }
          action={
            filtersActive ? (
              <Button
                type="button"
                variant="outline"
                tabIndex={0}
                aria-label="Clear filters"
                className="min-h-10"
                onClick={handleResetFilters}
              >
                Clear filters
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="space-y-2">
          {threads.map((thread) => (
            <ThreadCard key={thread.id} thread={thread} />
          ))}
          <div ref={sentinelRef} className="h-8" />
          {isFetchingNextPage ? <Skeleton className="h-16 w-full rounded-lg" /> : null}
        </div>
      )}
    </>
  )
}
