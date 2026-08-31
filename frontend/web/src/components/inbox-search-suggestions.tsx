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

type InboxSearchSuggestionsProps = {
  resultsId: string
  optionIdPrefix: string
  highlightedIndex: number
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
  onHighlight: (index: number) => void
}

export const countSelectableSuggestions = (args: {
  showValueSuggestions: boolean
  valueSuggestions: string[]
  showFilters: boolean
  filterKeys: SearchFilterKey[]
  showHits: boolean
  hits: SearchHit[]
}): number => {
  if (args.showValueSuggestions) return args.valueSuggestions.length
  if (args.showFilters) return args.filterKeys.length
  if (args.showHits) return args.hits.length
  return 0
}

export const InboxSearchSuggestions = ({
  resultsId,
  optionIdPrefix,
  highlightedIndex,
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
  onHighlight,
}: InboxSearchSuggestionsProps) => {
  let optionIndex = 0

  return (
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
        ? valueSuggestions.map((suggestion) => {
            const index = optionIndex
            optionIndex += 1
            const optionId = `${optionIdPrefix}-${index}`
            const selected = highlightedIndex === index
            return (
              <li key={`${valueEntryKey}-${suggestion}`}>
                <button
                  type="button"
                  id={optionId}
                  role="option"
                  aria-selected={selected}
                  tabIndex={0}
                  aria-label={`${valueEntryKey}:${suggestion}`}
                  className="hover:bg-muted/60 flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm"
                  onMouseDown={(event) => {
                    event.preventDefault()
                  }}
                  onMouseEnter={() => {
                    onHighlight(index)
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
            )
          })
        : null}
      {showFilters
        ? filterKeys.map((key) => {
            const index = optionIndex
            optionIndex += 1
            const optionId = `${optionIdPrefix}-${index}`
            const selected = highlightedIndex === index
            return (
              <li key={key}>
                <button
                  type="button"
                  id={optionId}
                  role="option"
                  aria-selected={selected}
                  tabIndex={0}
                  aria-label={`${key}: ${FILTER_VALUE_PROMPTS[key]}`}
                  className="hover:bg-muted/60 flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm"
                  onMouseDown={(event) => {
                    event.preventDefault()
                  }}
                  onMouseEnter={() => {
                    onHighlight(index)
                  }}
                  onClick={() => {
                    onInsertFilter(key)
                  }}
                >
                  <span className="text-foreground font-medium">{key}:</span>
                  <span className="text-muted-foreground text-xs">{FILTER_VALUE_PROMPTS[key]}</span>
                </button>
              </li>
            )
          })
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
        ? hits.map((hit) => {
            const index = optionIndex
            optionIndex += 1
            const optionId = `${optionIdPrefix}-${index}`
            const selected = highlightedIndex === index
            return (
              <li key={hit.thread_id} role="option" id={optionId} aria-selected={selected}>
                <SearchHitLink hit={hit} onNavigate={onNavigate} />
              </li>
            )
          })
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
}
