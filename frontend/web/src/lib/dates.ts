/** Reviewer-facing dates.

The Vercel i18n guideline is to use the browser locale. This product pins
`en-US` instead so calendar dates stay month/day (Kelvin: 8/10, not 10/8).
Ops-report YMD (`en-CA`) is unchanged.
*/

const REVIEWER_LOCALE = "en-US"

const dateFormatter = new Intl.DateTimeFormat(REVIEWER_LOCALE, {
  month: "numeric",
  day: "numeric",
  year: "numeric",
})

const dateTimeFormatter = new Intl.DateTimeFormat(REVIEWER_LOCALE, {
  month: "numeric",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
})

const calendarFormatter = new Intl.DateTimeFormat(REVIEWER_LOCALE, {
  month: "short",
  day: "numeric",
})

export const formatReviewerDate = (iso: string | null | undefined): string => {
  if (!iso) return "—"
  return dateFormatter.format(new Date(iso))
}

export const formatReviewerDateTime = (iso: string | null | undefined): string => {
  if (!iso) return "—"
  return dateTimeFormatter.format(new Date(iso))
}

export const formatRelativeTime = (iso: string | null | undefined): string => {
  if (!iso) return "—"
  const date = new Date(iso)
  const diffMs = Date.now() - date.getTime()
  const mins = Math.round(diffMs / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days}d ago`
  return calendarFormatter.format(date)
}
