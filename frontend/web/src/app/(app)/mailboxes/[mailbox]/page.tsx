"use client"

import { useInfiniteQuery } from "@tanstack/react-query"
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef } from "react"

import { Breadcrumbs } from "@/components/breadcrumbs"
import { EmptyState } from "@/components/empty-state"
import { ErrorPage } from "@/components/error-page"
import { ThreadCard } from "@/components/thread-card"
import { Button } from "@/components/ui/button"
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

export default function MailboxPage() {
  const params = useParams<{ mailbox: string }>()
  const mailbox = decodeURIComponent(params.mailbox)
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  // Filters live in the URL so they're shareable and survive back/forward nav.
  const state = searchParams.get("state") ?? ""
  const urgency = searchParams.get("urgency") ?? ""
  const staleOnly = searchParams.get("stale") === "1"
  const showFiltered = searchParams.get("filtered") === "1"

  const updateFilters = useCallback(
    (patch: { state?: string; urgency?: string; staleOnly?: boolean; showFiltered?: boolean }) => {
      const next = new URLSearchParams(searchParams.toString())
      const nextState = patch.state ?? state
      const nextUrgency = patch.urgency ?? urgency
      const nextStale = patch.staleOnly ?? staleOnly
      const nextFiltered = patch.showFiltered ?? showFiltered

      if (nextState) next.set("state", nextState)
      else next.delete("state")
      if (nextUrgency) next.set("urgency", nextUrgency)
      else next.delete("urgency")
      if (nextStale) next.set("stale", "1")
      else next.delete("stale")
      if (nextFiltered) next.set("filtered", "1")
      else next.delete("filtered")

      const query = next.toString()
      router.replace(query ? `${pathname}?${query}` : pathname)
    },
    [pathname, router, searchParams, state, urgency, staleOnly, showFiltered],
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
    queryKey: ["mailbox", mailbox, "threads", state, urgency, staleOnly, showFiltered],
    queryFn: ({ pageParam }) =>
      api.mailboxes.threads(mailbox, {
        state: state || undefined,
        urgency: urgency || undefined,
        stale_only: staleOnly || undefined,
        include_filtered: showFiltered || undefined,
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
  const filtersActive = Boolean(state || urgency || staleOnly || showFiltered)

  const handleToggleStale = () => {
    updateFilters({ staleOnly: !staleOnly })
  }

  const handleToggleFiltered = () => {
    updateFilters({ showFiltered: !showFiltered })
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
        <Button
          type="button"
          variant={staleOnly ? "default" : "outline"}
          size="sm"
          className="min-h-10"
          aria-pressed={staleOnly}
          onClick={handleToggleStale}
        >
          Stale only
        </Button>
        <Button
          type="button"
          variant={showFiltered ? "default" : "outline"}
          size="sm"
          className="min-h-10"
          aria-pressed={showFiltered}
          onClick={handleToggleFiltered}
        >
          {showFiltered ? "Showing spam/no-action" : "Show spam/no-action"}
        </Button>
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
