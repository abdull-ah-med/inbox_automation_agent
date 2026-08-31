/** Calendar YMD helpers shared by mailbox filters and ops reports. */

export const REPORT_TIMEZONE = "America/New_York"

export const formatYmdInTimezone = (date: Date, timeZone: string): string => {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date)
}

export const formatYmdLocal = (date: Date): string => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

export const parseYmdLocal = (ymd: string): Date => {
  const [year, month, day] = ymd.split("-").map(Number)
  return new Date(year, month - 1, day)
}

export const addLocalDays = (date: Date, days: number): Date => {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

export const addCalendarDays = (ymd: string, days: number): string => {
  const [year, month, day] = ymd.split("-").map(Number)
  const utc = new Date(Date.UTC(year, month - 1, day + days))
  return utc.toISOString().slice(0, 10)
}
