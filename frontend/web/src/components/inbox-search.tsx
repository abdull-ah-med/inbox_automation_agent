"use client"

import { useEffect, useId, useState, type KeyboardEvent } from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Search } from "lucide-react"
import { useRouter } from "next/navigation"

import {
  countSelectableSuggestions,
  InboxSearchSuggestions,
} from "@/components/inbox-search-suggestions"
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

const isModK = (event: KeyboardEvent | globalThis.KeyboardEvent) => {
  const key = event.key.toLowerCase()
  return key === "k" && (event.metaKey || event.ctrlKey) && !event.altKey
}

const nextHighlightIndex = (current: number, selectableCount: number): number => {
  const base = current >= 0 && current < selectableCount ? current : -1
  return (base + 1) % selectableCount
}

const prevHighlightIndex = (current: number, selectableCount: number): number => {
  const base = current >= 0 && current < selectableCount ? current : 0
  return base <= 0 ? selectableCount - 1 : base - 1
}

const focusSearchInput = () => {
  document.getElementById("inbox-search-input")?.focus()
}

const suggestionVisibility = (args: {
  open: boolean
  filterKeys: SearchFilterKey[]
  valueSuggestions: string[]
  searchable: boolean
  pendingFilter: SearchFilterKey | null
}) => {
  const showValueSuggestions = args.open && args.valueSuggestions.length > 0
  const showFilters =
    args.open && args.filterKeys.length > 0 && args.valueSuggestions.length === 0
  const showHits = args.open && args.searchable && args.valueSuggestions.length === 0
  const showPending =
    args.open && args.pendingFilter !== null && args.valueSuggestions.length === 0
  return {
    showValueSuggestions,
    showFilters,
    showHits,
    showPending,
    showList: showFilters || showHits || showPending || showValueSuggestions,
  }
}

export const InboxSearch = () => {
  const router = useRouter()
  const resultsId = useId()
  const optionIdPrefix = useId()
  const [value, setValue] = useState("")
  const [open, setOpen] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
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
          valueEntry.key === "direction" ? [...DIRECTION_FILTER_VALUES] : mailboxKeys,
          valueEntry.prefix,
        )

  const { showFilters, showHits, showPending, showValueSuggestions, showList } =
    suggestionVisibility({
      open,
      filterKeys,
      valueSuggestions,
      searchable,
      pendingFilter,
    })
  const selectableCount = countSelectableSuggestions({
    showValueSuggestions,
    valueSuggestions,
    showFilters,
    filterKeys,
    showHits,
    hits,
  })
  const safeHighlightedIndex =
    highlightedIndex >= 0 && highlightedIndex < selectableCount ? highlightedIndex : -1
  const activeOptionId =
    safeHighlightedIndex >= 0 ? `${optionIdPrefix}-${safeHighlightedIndex}` : undefined

  useEffect(() => {
    const handleWindowKeyDown = (event: globalThis.KeyboardEvent) => {
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
    setHighlightedIndex(-1)
    router.push(`/search?q=${encodeURIComponent(trimmed)}`)
  }

  const handleFocus = () => {
    setOpen(true)
  }

  const handleBlur = () => {
    window.setTimeout(() => {
      setOpen(false)
      setHighlightedIndex(-1)
    }, 120)
  }

  const handleInsertFilter = (key: SearchFilterKey) => {
    setValue(insertFilterKey(value, key))
    setHighlightedIndex(-1)
    setOpen(true)
    window.requestAnimationFrame(focusSearchInput)
  }

  const handleInsertFilterValue = (filterValue: string) => {
    if (valueEntry === null) return
    const withoutPartial = value.replace(new RegExp(`(${valueEntry.key}:)\\s*[^\\s]*$`, "i"), `$1`)
    setValue(completePendingFilter(withoutPartial, filterValue))
    setHighlightedIndex(-1)
    setOpen(true)
    window.requestAnimationFrame(focusSearchInput)
  }

  const handleNavigate = () => {
    setOpen(false)
    setHighlightedIndex(-1)
  }

  const handleHighlight = (index: number) => {
    setHighlightedIndex(index)
  }

  const handleActivateHighlighted = () => {
    if (safeHighlightedIndex < 0) return
    if (showValueSuggestions) {
      const suggestion = valueSuggestions[safeHighlightedIndex]
      if (suggestion) handleInsertFilterValue(suggestion)
      return
    }
    if (showFilters) {
      const key = filterKeys[safeHighlightedIndex]
      if (key) handleInsertFilter(key)
      return
    }
    const hit = hits[safeHighlightedIndex]
    if (!hit) return
    setOpen(false)
    setHighlightedIndex(-1)
    router.push(`/threads/${hit.thread_id}`)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!showList || selectableCount === 0) return
    if (event.key === "ArrowDown") {
      event.preventDefault()
      setHighlightedIndex((current) => nextHighlightIndex(current, selectableCount))
      return
    }
    if (event.key === "ArrowUp") {
      event.preventDefault()
      setHighlightedIndex((current) => prevHighlightIndex(current, selectableCount))
      return
    }
    if (event.key === "Enter" && safeHighlightedIndex >= 0) {
      event.preventDefault()
      handleActivateHighlighted()
    }
  }

  return (
    <form role="search" onSubmit={handleSubmit} className="relative min-w-0 flex-1">
      <label htmlFor="inbox-search-input" className="sr-only">
        Search mail
      </label>
      <Search
        className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
        aria-hidden="true"
      />
      <Input
        id="inbox-search-input"
        type="search"
        name="q"
        value={value}
        autoComplete="off"
        placeholder="from: subject: contains: mailbox:"
        aria-label="Search mail"
        aria-autocomplete="list"
        aria-expanded={showList}
        aria-controls={resultsId}
        aria-activedescendant={activeOptionId}
        onChange={(event) => {
          setValue(event.target.value)
          setHighlightedIndex(-1)
          setOpen(true)
        }}
        onFocus={handleFocus}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
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
        <InboxSearchSuggestions
          resultsId={resultsId}
          optionIdPrefix={optionIdPrefix}
          highlightedIndex={safeHighlightedIndex}
          showPending={showPending}
          pendingFilter={pendingFilter}
          showValueSuggestions={showValueSuggestions}
          valueSuggestions={valueSuggestions}
          valueEntryKey={valueEntry?.key}
          showFilters={showFilters}
          filterKeys={filterKeys}
          showHits={showHits}
          isFetching={resultsQuery.isFetching}
          isError={resultsQuery.isError}
          isSuccess={resultsQuery.isSuccess}
          hits={hits}
          trimmed={trimmed}
          onInsertFilter={handleInsertFilter}
          onInsertFilterValue={handleInsertFilterValue}
          onNavigate={handleNavigate}
          onHighlight={handleHighlight}
        />
      ) : null}
    </form>
  )
}
