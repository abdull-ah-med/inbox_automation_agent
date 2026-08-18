"use client"

import { useEffect, useId, useState } from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Search } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"

import { SearchHitLink } from "@/components/search-hit-link"
import { Input } from "@/components/ui/input"
import { Kbd } from "@/components/ui/kbd"
import { SEARCH_DEBOUNCE_MS, useDebouncedValue } from "@/hooks/use-debounced-value"
import { api } from "@/lib/api-client"
import {
  DIRECTION_FILTER_VALUES,
  completePendingFilter,
  filterValueEntry,
  filterValueSuggestions,
  incompleteFilterKey,
  insertFilterKey,
  isSearchableQuery,
  matchingFilterKeys,
  sanitizeSearchInput,
  type SearchFilterKey,
} from "@/lib/search-query"
import { cn } from "@/lib/utils"

const isModK = (event: KeyboardEvent) => {
  const key = event.key.toLowerCase()
  return key === "k" && (event.metaKey || event.ctrlKey) && !event.altKey
}

const FILTER_VALUE_PROMPTS: Record<SearchFilterKey, string> = {
  from: "Type a sender name or email",
  contains: "Type text to find in the body",
  subject: "Type a subject word",
  direction: "Type inbound or outbound",
  mailbox: "Type an inbox name",
}

export const InboxSearch = () => {
  const router = useRouter()
  const resultsId = useId()
  const [value, setValue] = useState("")
  const [open, setOpen] = useState(false)
  const trimmed = sanitizeSearchInput(value)
  const debounced = useDebouncedValue(trimmed, SEARCH_DEBOUNCE_MS)
  const filterKeys = matchingFilterKeys(value)
  const pendingFilter = incompleteFilterKey(value)
  const valueEntry = filterValueEntry(value)
  const searchable = isSearchableQuery(trimmed)

  const mailboxesQuery = useQuery({
    queryKey: ["mailboxes", "list"],
    queryFn: () => api.mailboxes.list(),
    enabled: open,
    staleTime: 60_000,
  })

  const resultsQuery = useQuery({
    queryKey: ["inbox-search", debounced],
    queryFn: () => api.search.threads({ q: debounced }),
    enabled: isSearchableQuery(debounced),
    placeholderData: keepPreviousData,
  })
  const hits = resultsQuery.data?.hits ?? []

  const mailboxKeys = (mailboxesQuery.data ?? []).map((item) => item.mailbox)
  const valueSuggestions =
    valueEntry === null
      ? []
      : filterValueSuggestions(
          valueEntry.key,
          valueEntry.key === "direction"
            ? [...DIRECTION_FILTER_VALUES]
            : mailboxKeys,
          valueEntry.prefix,
        )

  useEffect(() => {
    const handleWindowKeyDown = (event: KeyboardEvent) => {
      if (!isModK(event)) return
      event.preventDefault()
      document.getElementById("inbox-search-input")?.focus()
      setOpen(true)
    }
    window.addEventListener("keydown", handleWindowKeyDown)
    return () => window.removeEventListener("keydown", handleWindowKeyDown)
  }, [])

  const handleSubmit = (event: { preventDefault: () => void }) => {
    event.preventDefault()
    if (!searchable) return
    setOpen(false)
    router.push(`/search?q=${encodeURIComponent(trimmed)}`)
  }

  const handleFocus = () => {
    setOpen(true)
  }

  const handleBlur = () => {
    window.setTimeout(() => setOpen(false), 120)
  }

  const handleInsertFilter = (key: SearchFilterKey) => {
    setValue(insertFilterKey(value, key))
    setOpen(true)
    window.requestAnimationFrame(() => {
      document.getElementById("inbox-search-input")?.focus()
    })
  }

  const handleInsertFilterValue = (filterValue: string) => {
    if (valueEntry === null) return
    const withoutPartial = value.replace(
      new RegExp(`(${valueEntry.key}:)\\s*[^\\s]*$`, "i"),
      `$1`,
    )
    setValue(completePendingFilter(withoutPartial, filterValue))
    setOpen(true)
    window.requestAnimationFrame(() => {
      document.getElementById("inbox-search-input")?.focus()
    })
  }

  const showFilters = open && filterKeys.length > 0 && valueSuggestions.length === 0
  const showHits = open && searchable && valueSuggestions.length === 0
  const showPending =
    open && pendingFilter !== null && valueSuggestions.length === 0
  const showValueSuggestions = open && valueSuggestions.length > 0
  const showList =
    showFilters || showHits || showPending || showValueSuggestions

  return (
    <form
      role="search"
      onSubmit={handleSubmit}
      className="relative min-w-0 flex-1"
    >
      <label htmlFor="inbox-search-input" className="sr-only">
        Search mail
      </label>
      <Search
        className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-gray-400"
        aria-hidden="true"
      />
      <Input
        id="inbox-search-input"
        type="search"
        role="searchbox"
        name="q"
        value={value}
        autoComplete="off"
        placeholder="from: subject: contains: mailbox:"
        aria-label="Search mail"
        aria-autocomplete="list"
        aria-controls={resultsId}
        aria-expanded={showList}
        onChange={(event) => {
          setValue(event.target.value)
          setOpen(true)
        }}
        onFocus={handleFocus}
        onBlur={handleBlur}
        className={cn(
          "h-10 w-full min-w-0 rounded-xl border-gray-200 bg-gray-50 pr-3 pl-9 text-sm sm:pr-16",
          "dark:border-gray-700 dark:bg-gray-950",
          "[&::-webkit-search-cancel-button]:cursor-pointer",
        )}
      />
      <Kbd className="pointer-events-none absolute top-1/2 right-2 hidden -translate-y-1/2 sm:inline-flex">
        ⌘K
      </Kbd>
      {showList ? (
        <ul
          id={resultsId}
          role="listbox"
          aria-label="Search suggestions"
          className="absolute z-50 mt-2 max-h-80 w-full overflow-y-auto rounded-xl bg-popover py-1 shadow-lg ring-1 ring-foreground/10"
        >
          {showPending && pendingFilter ? (
            <li className="px-3 py-2 text-sm text-muted-foreground">
              {FILTER_VALUE_PROMPTS[pendingFilter]}
            </li>
          ) : null}
          {showValueSuggestions
            ? valueSuggestions.map((suggestion) => (
                <li key={`${valueEntry?.key}-${suggestion}`}>
                  <button
                    type="button"
                    role="option"
                    tabIndex={0}
                    aria-label={`${valueEntry?.key}:${suggestion}`}
                    className="flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm hover:bg-muted/60"
                    onMouseDown={(event) => {
                      event.preventDefault()
                    }}
                    onClick={() => {
                      handleInsertFilterValue(suggestion)
                    }}
                  >
                    <span className="font-medium text-foreground">
                      {valueEntry?.key}:{suggestion}
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
                    tabIndex={0}
                    aria-label={`${key}: ${FILTER_VALUE_PROMPTS[key]}`}
                    className="flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm hover:bg-muted/60"
                    onMouseDown={(event) => {
                      event.preventDefault()
                    }}
                    onClick={() => {
                      handleInsertFilter(key)
                    }}
                  >
                    <span className="font-medium text-foreground">{key}:</span>
                    <span className="text-xs text-muted-foreground">
                      {FILTER_VALUE_PROMPTS[key]}
                    </span>
                  </button>
                </li>
              ))
            : null}
          {showHits && resultsQuery.isFetching && hits.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted-foreground">Searching…</li>
          ) : null}
          {showHits && resultsQuery.isError ? (
            <li className="px-3 py-2 text-sm text-muted-foreground">
              Search failed. Try again in a moment.
            </li>
          ) : null}
          {showHits && resultsQuery.isSuccess && hits.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted-foreground">No matching threads</li>
          ) : null}
          {showHits
            ? hits.map((hit) => (
                <li key={hit.thread_id} role="option">
                  <SearchHitLink hit={hit} onNavigate={() => setOpen(false)} />
                </li>
              ))
            : null}
          {showHits && hits.length > 0 ? (
            <li className="border-t border-border">
              <Link
                href={`/search?q=${encodeURIComponent(trimmed)}`}
                tabIndex={0}
                aria-label="See all search results"
                className="block px-3 py-2 text-sm font-medium text-foreground hover:bg-muted/60"
                onMouseDown={(event) => {
                  event.preventDefault()
                }}
                onClick={() => {
                  setOpen(false)
                }}
              >
                See all results
              </Link>
            </li>
          ) : null}
        </ul>
      ) : null}
    </form>
  )
}
