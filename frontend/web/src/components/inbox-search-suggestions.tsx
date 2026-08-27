"use client"

import Link from "next/link"

import { SearchHitLink } from "@/components/search-hit-link"
import type { SearchHit } from "@/lib/types"
import type { SearchFilterKey } from "@/lib/search-query"

const FILTER_VALUE_PROMPTS: Record<SearchFilterKey, string> = {
  from: "Type a sender name or email",
  contains: "Type text to find in the body",
  subject: "Type a subject word",
  direction: "Type inbound or outbound",
  mailbox: "Type an inbox name",
}

export { FILTER_VALUE_PROMPTS }

type InboxSearchSuggestionsProps = {
  resultsId: string
  showPending: boolean
  pendingFilter: SearchFilterKey | null
  showValueSuggestions: boolean
  valueSuggestions: string[]
  valueEntryKey: SearchFilterKey | undefined
  showFilters: boolean
  filterKeys: SearchFilterKey[]
  showHits: boolean
  isFetching: boolean
  isError: boolean
  isSuccess: boolean
  hits: SearchHit[]
  trimmed: string
  onInsertFilter: (key: SearchFilterKey) => void
  onInsertFilterValue: (filterValue: string) => void
  onNavigate: () => void
}

export const InboxSearchSuggestions = ({
  resultsId,
  showPending,
  pendingFilter,
  showValueSuggestions,
  valueSuggestions,
  valueEntryKey,
  showFilters,
  filterKeys,
  showHits,
  isFetching,
  isError,
  isSuccess,
  hits,
  trimmed,
  onInsertFilter,
  onInsertFilterValue,
  onNavigate,
}: InboxSearchSuggestionsProps) => (
  <ul
    id={resultsId}
    role="listbox"
    aria-label="Search suggestions"
    className="bg-popover ring-foreground/10 absolute z-50 mt-2 max-h-80 w-full overflow-y-auto rounded-xl py-1 shadow-lg ring-1"
  >
    {showPending && pendingFilter ? (
      <li className="text-muted-foreground px-3 py-2 text-sm">
        {FILTER_VALUE_PROMPTS[pendingFilter]}
      </li>
    ) : null}
    {showValueSuggestions
      ? valueSuggestions.map((suggestion) => (
          <li key={`${valueEntryKey}-${suggestion}`}>
            <button
              type="button"
              role="option"
              aria-selected="false"
              tabIndex={0}
              aria-label={`${valueEntryKey}:${suggestion}`}
              className="hover:bg-muted/60 flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm"
              onMouseDown={(event) => {
                event.preventDefault()
              }}
              onClick={() => {
                onInsertFilterValue(suggestion)
              }}
            >
              <span className="text-foreground font-medium">
                {valueEntryKey}:{suggestion}
              </span>
            </button>
          </li>
        ))
      : null}
    {showFilters
      ? filterKeys.map((key) => (
          <li key={key}>
            <button
              type="button"
              role="option"
              aria-selected="false"
              tabIndex={0}
              aria-label={`${key}: ${FILTER_VALUE_PROMPTS[key]}`}
              className="hover:bg-muted/60 flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm"
              onMouseDown={(event) => {
                event.preventDefault()
              }}
              onClick={() => {
                onInsertFilter(key)
              }}
            >
              <span className="text-foreground font-medium">{key}:</span>
              <span className="text-muted-foreground text-xs">{FILTER_VALUE_PROMPTS[key]}</span>
            </button>
          </li>
        ))
      : null}
    {showHits && isFetching && hits.length === 0 ? (
      <li className="text-muted-foreground px-3 py-2 text-sm">Searching…</li>
    ) : null}
    {showHits && isError ? (
      <li className="text-muted-foreground px-3 py-2 text-sm">
        Search failed. Try again in a moment.
      </li>
    ) : null}
    {showHits && isSuccess && hits.length === 0 ? (
      <li className="text-muted-foreground px-3 py-2 text-sm">No matching threads</li>
    ) : null}
    {showHits
      ? hits.map((hit) => (
          <li key={hit.thread_id} role="option" aria-selected="false">
            <SearchHitLink hit={hit} onNavigate={onNavigate} />
          </li>
        ))
      : null}
    {showHits && hits.length > 0 ? (
      <li className="border-border border-t">
        <Link
          href={`/search?q=${encodeURIComponent(trimmed)}`}
          tabIndex={0}
          aria-label="See all search results"
          className="text-foreground hover:bg-muted/60 block px-3 py-2 text-sm font-medium"
          onMouseDown={(event) => {
            event.preventDefault()
          }}
          onClick={onNavigate}
        >
          See all results
        </Link>
      </li>
    ) : null}
  </ul>
)
