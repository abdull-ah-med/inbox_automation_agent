"use client"

import { useInfiniteQuery } from "@tanstack/react-query"
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef } from "react"

import { Breadcrumbs } from "@/components/breadcrumbs"
import { EmptyState } from "@/components/empty-state"
import { PageTransition } from "@/components/motion"
import { ThreadCard } from "@/components/thread-card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"
import { inboxColor, inboxLabel } from "@/lib/design-tokens"

const STATE_OPTIONS = [
  { value: "", label: "All actionable states" },
  { value: "NEW", label: "New — not yet triaged" },
  { value: "AWAITING_CLIENT", label: "Awaiting client" },
  { value: "AWAITING_VENDOR", label: "Awaiting vendor" },
  { value: "AWAITING_PARTNER", label: "Awaiting partner" },
  { value: "DRAFTED", label: "Draft ready" },
  { value: "REQUIRES_HUMAN", label: "Needs human review" },
  { value: "RESOLVED", label: "Resolved" },
  { value: "NO_ACTION", label: "No action needed" },
  { value: "SPAM", label: "Spam" },
]
const URGENCIES = ["", "CRITICAL", "HIGH", "NORMAL", "LOW"]

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
    (patch: {
      state?: string
      urgency?: string
      staleOnly?: boolean
      showFiltered?: boolean
    }) => {
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

  const query = useInfiniteQuery({
    queryKey: [
      "mailbox",
      mailbox,
      "threads",
      state,
      urgency,
      staleOnly,
      showFiltered,
    ],
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

  const threads = useMemo(
    () => query.data?.pages.flatMap((p) => p.items) ?? [],
    [query.data],
  )

  useEffect(() => {
    const node = sentinelRef.current
    if (!node) return
    const observer = new IntersectionObserver((entries) => {
      if (
        entries[0]?.isIntersecting &&
        query.hasNextPage &&
        !query.isFetchingNextPage
      ) {
        void query.fetchNextPage()
      }
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [query])

  const color = inboxColor(mailbox)
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
    <PageTransition>
      <Breadcrumbs
        items={[
          { label: "Overview", href: "/dashboard" },
          { label },
        ]}
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span
          className="rounded-full px-2 py-0.5 text-xs font-medium text-white"
          style={{ backgroundColor: color }}
        >
          {label}
        </span>
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Threads
        </h2>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select
          aria-label="Filter by state"
          className="rounded-md border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
          value={state}
          onChange={(e) => updateFilters({ state: e.target.value })}
        >
          {STATE_OPTIONS.map((s) => (
            <option key={s.value || "all"} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by urgency"
          className="rounded-md border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
          value={urgency}
          onChange={(e) => updateFilters({ urgency: e.target.value })}
        >
          {URGENCIES.map((u) => (
            <option key={u || "all"} value={u}>
              {u ? u : "All urgency"}
            </option>
          ))}
        </select>
        <Button
          type="button"
          variant={staleOnly ? "default" : "outline"}
          size="sm"
          aria-pressed={staleOnly}
          onClick={handleToggleStale}
        >
          Stale only
        </Button>
        <Button
          type="button"
          variant={showFiltered ? "default" : "outline"}
          size="sm"
          aria-pressed={showFiltered}
          onClick={handleToggleFiltered}
        >
          {showFiltered ? "Showing spam/no-action" : "Show spam/no-action"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={handleResetFilters}
        >
          Reset
        </Button>
      </div>

      {query.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : query.isError ? (
        <EmptyState
          title="Couldn’t load threads"
          description={
            query.error instanceof Error
              ? query.error.message
              : "Something went wrong loading this mailbox."
          }
        />
      ) : threads.length === 0 ? (
        <EmptyState
          title={filtersActive ? "No matching threads" : "No mail in this inbox"}
          description={
            filtersActive
              ? "Try clearing filters to see all threads for this mailbox."
              : `${label} has no ingested threads yet. New mail will appear here after the poller runs.`
          }
        />
      ) : (
        <div className="space-y-2">
          {threads.map((thread) => (
            <ThreadCard key={thread.id} thread={thread} />
          ))}
          <div ref={sentinelRef} className="h-8" />
          {query.isFetchingNextPage ? (
            <Skeleton className="h-16 w-full rounded-lg" />
          ) : null}
        </div>
      )}
    </PageTransition>
  )
}
