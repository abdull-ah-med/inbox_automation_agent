"use client"

import { Suspense } from "react"
import { useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"

import { EmptyState } from "@/components/empty-state"
import { SearchHitLink } from "@/components/search-hit-link"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"
import { sanitizeSearchInput, isSearchableQuery } from "@/lib/search-query"

const SearchResults = () => {
  const searchParams = useSearchParams()
  const q = sanitizeSearchInput(searchParams.get("q") ?? "")
  const searchable = isSearchableQuery(q)

  const resultsQuery = useQuery({
    queryKey: ["inbox-search-page", q],
    queryFn: () => api.search.threads({ q }),
    enabled: searchable,
  })
  const hits = resultsQuery.data?.hits ?? []

  const handleRetry = () => {
    void resultsQuery.refetch()
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          Mail
        </p>
        <h1 className="text-xl font-semibold tracking-tight">Search</h1>
        {searchable ? (
          <p className="mt-1 text-sm text-muted-foreground">Results for “{q}”</p>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">
            Type a keyword or stack filters like Discord: from: contains: subject:
            direction: mailbox:
          </p>
        )}
      </div>

      {!searchable ? (
        <EmptyState
          title="Search the inbox"
          description="Use the search box in the header. InboxAssistant is for questions and overviews."
        />
      ) : null}

      {searchable && resultsQuery.isFetching && hits.length === 0 ? (
        <div className="space-y-2" aria-busy="true" aria-live="polite">
          <Skeleton className="h-20 rounded-xl" />
          <Skeleton className="h-20 rounded-xl" />
        </div>
      ) : null}

      {searchable && resultsQuery.isSuccess && hits.length === 0 ? (
        <EmptyState
          title="No matching threads"
          description="Try a name, subject word, filter, or invoice number."
        />
      ) : null}

      {hits.length > 0 ? (
        <ul className="divide-y divide-border overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
          {hits.map((hit) => (
            <li key={hit.thread_id}>
              <SearchHitLink hit={hit} />
            </li>
          ))}
        </ul>
      ) : null}

      {searchable && resultsQuery.isError ? (
        <EmptyState
          title="Search failed"
          description="Try again in a moment."
          action={
            <Button type="button" variant="outline" onClick={handleRetry}>
              Retry search
            </Button>
          }
        />
      ) : null}
    </div>
  )
}

export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-2" aria-busy="true">
          <Skeleton className="h-8 w-40 rounded-lg" />
          <Skeleton className="h-20 rounded-xl" />
        </div>
      }
    >
      <SearchResults />
    </Suspense>
  )
}
