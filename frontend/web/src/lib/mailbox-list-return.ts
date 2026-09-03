/** Mailbox list path including active filter query string. */
export const mailboxListReturnTo = (pathname: string, search: string): string =>
  search ? `${pathname}?${search}` : pathname

/** Accept only in-app mailbox list paths (no open redirects). */
export const parseMailboxReturnTo = (value: string | null): string | null => {
  if (!value || !value.startsWith("/mailboxes/")) return null
  if (value.includes("://") || value.startsWith("//")) return null
  return value
}

export const threadHrefWithMailboxReturn = (threadId: string, returnTo?: string): string => {
  if (!returnTo) return `/threads/${threadId}`
  const params = new URLSearchParams({ returnTo })
  return `/threads/${threadId}?${params.toString()}`
}
