"use client"

import { useEffect, useId, useState } from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Search } from "lucide-react"
import { useRouter } from "next/navigation"

import { InboxSearchSuggestions } from "@/components/inbox-search-suggestions"
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
          valueEntry.key === "direction" ? [...DIRECTION_FILTER_VALUES] : mailboxKeys,
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
    const withoutPartial = value.replace(new RegExp(`(${valueEntry.key}:)\\s*[^\\s]*$`, "i"), `$1`)
    setValue(completePendingFilter(withoutPartial, filterValue))
    setOpen(true)
    window.requestAnimationFrame(() => {
      document.getElementById("inbox-search-input")?.focus()
    })
  }

  const handleNavigate = () => {
    setOpen(false)
  }

  const showFilters = open && filterKeys.length > 0 && valueSuggestions.length === 0
  const showHits = open && searchable && valueSuggestions.length === 0
  const showPending = open && pendingFilter !== null && valueSuggestions.length === 0
  const showValueSuggestions = open && valueSuggestions.length > 0
  const showList = showFilters || showHits || showPending || showValueSuggestions

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
        aria-controls={resultsId}
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
        <InboxSearchSuggestions
          resultsId={resultsId}
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
        />
      ) : null}
    </form>
  )
}
