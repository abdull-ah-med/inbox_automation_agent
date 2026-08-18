export const SEARCH_FILTER_KEYS = [
  "from",
  "contains",
  "subject",
  "direction",
  "mailbox",
] as const

export type SearchFilterKey = (typeof SEARCH_FILTER_KEYS)[number]

export type ParsedSearchQuery = {
  freeText: string
  senders: string[]
  contains: string[]
  subjects: string[]
  directions: string[]
  mailboxes: string[]
}

const FILTER_KEYS_PATTERN = "from|contains|subject|direction|mailbox"

const FILTER_RE = new RegExp(
  `(?:^|\\s)(${FILTER_KEYS_PATTERN}):\\s*(?:"([^"]*)"|(?!(?:${FILTER_KEYS_PATTERN}):)([^\\s]+))`,
  "gi",
)

const DANGLING_FILTER_RE = new RegExp(
  `(?:^|\\s)(?:${FILTER_KEYS_PATTERN}):\\s*`,
  "gi",
)

const DIRECTION_ALIASES: Record<string, "inbound" | "outbound"> = {
  inbound: "inbound",
  in: "inbound",
  incoming: "inbound",
  outbound: "outbound",
  out: "outbound",
  sent: "outbound",
  outgoing: "outbound",
}

const stripHtmlAndControls = (value: string): string => {
  const withoutTags = value.replace(/<[^>]*>/g, " ")
  return withoutTags.replace(
    /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g,
    "",
  )
}

const collapse = (value: string): string => value.replace(/\s+/g, " ").trim()

const sanitizeValue = (value: string): string => {
  return collapse(value.replace(/[&|!():*'"]/g, " "))
}

const sanitizeFreeText = (value: string): string => {
  return collapse(value.replace(/[&|!()*']/g, " "))
}

export const parseSearchQuery = (raw: string): ParsedSearchQuery => {
  FILTER_RE.lastIndex = 0
  DANGLING_FILTER_RE.lastIndex = 0
  const text = stripHtmlAndControls(raw)
  const senders: string[] = []
  const contains: string[] = []
  const subjects: string[] = []
  const directions: string[] = []
  const mailboxes: string[] = []

  const remainder = text
    .replace(FILTER_RE, (_match, key, quoted, bare) => {
      const value = sanitizeValue(quoted ?? bare ?? "")
      if (!value) return " "
      const filterKey = String(key).toLowerCase() as SearchFilterKey
      if (filterKey === "from") senders.push(value)
      if (filterKey === "contains") contains.push(value)
      if (filterKey === "subject") subjects.push(value)
      if (filterKey === "direction") {
        const direction = DIRECTION_ALIASES[value.toLowerCase()]
        if (direction && !directions.includes(direction)) directions.push(direction)
      }
      if (filterKey === "mailbox") mailboxes.push(value)
      return " "
    })
    .replace(DANGLING_FILTER_RE, " ")

  return {
    freeText: sanitizeFreeText(remainder),
    senders,
    contains,
    subjects,
    directions,
    mailboxes,
  }
}

export const sanitizeSearchInput = (value: string): string => {
  return collapse(
    stripHtmlAndControls(value).replace(/[&|!()*']/g, " "),
  )
}

export const isSearchableQuery = (raw: string): boolean => {
  const parsed = parseSearchQuery(raw)
  return Boolean(
    parsed.freeText ||
      parsed.senders.length ||
      parsed.contains.length ||
      parsed.subjects.length ||
      parsed.directions.length ||
      parsed.mailboxes.length,
  )
}

export const incompleteFilterKey = (value: string): SearchFilterKey | null => {
  const match = value.match(
    new RegExp(`(?:^|\\s)(${FILTER_KEYS_PATTERN}):\\s*$`, "i"),
  )
  if (!match) return null
  return match[1].toLowerCase() as SearchFilterKey
}

export const filterValueEntry = (
  value: string,
): { key: SearchFilterKey; prefix: string } | null => {
  const match = value.match(
    new RegExp(`(?:^|\\s)(${FILTER_KEYS_PATTERN}):\\s*([^\\s]*)$`, "i"),
  )
  if (!match) return null
  const key = match[1].toLowerCase() as SearchFilterKey
  if (key !== "direction" && key !== "mailbox") return null
  return { key, prefix: match[2] ?? "" }
}

export const matchingFilterKeys = (value: string): SearchFilterKey[] => {
  if (incompleteFilterKey(value)) return []
  if (!value.trim()) return [...SEARCH_FILTER_KEYS]
  if (value.endsWith(" ")) return [...SEARCH_FILTER_KEYS]
  const parts = value.trimEnd().split(/\s+/)
  const last = parts[parts.length - 1] ?? ""
  const colonAt = last.indexOf(":")
  if (colonAt >= 0) {
    const key = last.slice(0, colonAt).toLowerCase()
    const rest = last.slice(colonAt + 1)
    if (
      SEARCH_FILTER_KEYS.includes(key as SearchFilterKey) &&
      rest.length > 0
    ) {
      return []
    }
    if (SEARCH_FILTER_KEYS.includes(key as SearchFilterKey) && rest.length === 0) {
      return []
    }
  }
  const lower = last.toLowerCase()
  return SEARCH_FILTER_KEYS.filter(
    (key) => key.startsWith(lower) || `${key}:`.startsWith(lower),
  )
}

export const insertFilterKey = (value: string, key: SearchFilterKey): string => {
  if (!value.trim()) return `${key}:`
  if (value.endsWith(" ")) return `${value}${key}:`
  const parts = value.split(/(\s+)/)
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    const token = parts[index]
    if (!token.trim()) continue
    const lower = token.toLowerCase()
    const isPartialOperator =
      !lower.includes(":") &&
      (key.startsWith(lower) || `${key}:`.startsWith(lower))
    if (isPartialOperator) {
      parts[index] = `${key}:`
      return parts.join("")
    }
    return `${value} ${key}:`
  }
  return `${key}:`
}

export const completePendingFilter = (
  value: string,
  filterValue: string,
): string => {
  if (!incompleteFilterKey(value)) return value
  return `${value}${filterValue} `
}

export const filterValueSuggestions = (
  key: SearchFilterKey,
  candidates: string[],
  typedPrefix: string,
): string[] => {
  if (key !== "direction" && key !== "mailbox") return []
  const needle = typedPrefix.trim().toLowerCase()
  if (!needle) return [...candidates]
  return candidates.filter((item) => item.toLowerCase().startsWith(needle))
}

export const DIRECTION_FILTER_VALUES = ["inbound", "outbound"] as const

