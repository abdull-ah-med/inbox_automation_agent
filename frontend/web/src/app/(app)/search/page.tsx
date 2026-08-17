"use client"

import { Suspense } from "react"
import { useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Search } from "lucide-react"

import { EmptyState } from "@/components/empty-state"
import { SearchHitLink } from "@/components/search-hit-link"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"

const SearchResults = () => {
  const searchParams = useSearchParams()
  const q = (searchParams.get("q") ?? "").trim()

  const resultsQuery = useQuery({
    queryKey: ["inbox-search-page", q],
    queryFn: () => api.search.threads({ q }),
    enabled: q.length > 0,
  })
  const hits = resultsQuery.data?.hits ?? []

  return (
    <div className="space-y-4">
      <div>
        <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          Mail
        </p>
        <h1 className="text-xl font-semibold tracking-tight">Search</h1>
        {q ? (
          <p className="mt-1 text-sm text-muted-foreground">Results for “{q}”</p>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">
            Type a keyword in the search box. This is plain-text search, like Outlook.
          </p>
        )}
      </div>

      {!q ? (
        <EmptyState
          title="Search the inbox"
          description="Use the search box in the header. InboxAssistant is for questions and overviews."
        />
      ) : null}

      {q && resultsQuery.isFetching && hits.length === 0 ? (
        <div className="space-y-2" aria-busy="true" aria-live="polite">
          <Skeleton className="h-20 rounded-xl" />
          <Skeleton className="h-20 rounded-xl" />
        </div>
      ) : null}

      {q && resultsQuery.isSuccess && hits.length === 0 ? (
        <EmptyState
          title="No matching threads"
          description="Try a name, subject word, or invoice number."
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

      {q && resultsQuery.isError ? (
        <EmptyState
          title="Search failed"
          description="Try again in a moment."
          action={
            <Search className="mx-auto size-4 text-muted-foreground" aria-hidden="true" />
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
