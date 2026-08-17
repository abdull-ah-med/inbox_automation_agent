"use client"

import { useEffect, useId, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Search } from "lucide-react"
import { useRouter } from "next/navigation"

import { SearchHitLink } from "@/components/search-hit-link"
import { Input } from "@/components/ui/input"
import { Kbd } from "@/components/ui/kbd"
import { api } from "@/lib/api-client"
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
  const trimmed = value.trim()

  const resultsQuery = useQuery({
    queryKey: ["inbox-search", trimmed],
    queryFn: () => api.search.threads({ q: trimmed }),
    enabled: trimmed.length > 0,
  })
  const hits = resultsQuery.data?.hits ?? []

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
    if (!trimmed) return
    setOpen(false)
    router.push(`/search?q=${encodeURIComponent(trimmed)}`)
  }

  const handleFocus = () => {
    if (trimmed) setOpen(true)
  }

  const handleBlur = () => {
    window.setTimeout(() => setOpen(false), 120)
  }

  const showList = open && trimmed.length > 0

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
        placeholder="Search mail"
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
          "h-10 rounded-xl border-gray-200 bg-gray-50 pr-16 pl-9 text-sm",
          "dark:border-gray-700 dark:bg-gray-950",
        )}
      />
      <Kbd className="pointer-events-none absolute top-1/2 right-2 hidden -translate-y-1/2 sm:inline-flex">
        ⌘K
      </Kbd>
      {showList ? (
        <ul
          id={resultsId}
          role="listbox"
          aria-label="Search results"
          className="absolute z-50 mt-2 max-h-80 w-full overflow-y-auto rounded-xl bg-popover py-1 shadow-lg ring-1 ring-foreground/10"
        >
          {resultsQuery.isFetching && hits.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted-foreground">Searching…</li>
          ) : null}
          {resultsQuery.isSuccess && hits.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted-foreground">No matching threads</li>
          ) : null}
          {hits.map((hit) => (
            <li key={hit.thread_id} role="option">
              <SearchHitLink hit={hit} onNavigate={() => setOpen(false)} />
            </li>
          ))}
        </ul>
      ) : null}
    </form>
  )
}
