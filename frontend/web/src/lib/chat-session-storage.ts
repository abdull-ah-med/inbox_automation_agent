const SESSION_KEY_PREFIX = "inboxassistant_session_"

export const chatSessionStorageKey = (mailbox: string): string =>
  `${SESSION_KEY_PREFIX}${mailbox || "all"}`

export const readStoredSessionId = (mailbox: string): string | null => {
  if (typeof window === "undefined") return null
  try {
    const value = window.localStorage.getItem(chatSessionStorageKey(mailbox))
    return value?.trim() || null
  } catch {
    return null
  }
}

export const writeStoredSessionId = (mailbox: string, sessionId: string): void => {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(chatSessionStorageKey(mailbox), sessionId)
  } catch {
    // ignore quota / private mode
  }
}

export const clearStoredSessionId = (mailbox: string): void => {
  if (typeof window === "undefined") return
  try {
    window.localStorage.removeItem(chatSessionStorageKey(mailbox))
  } catch {
    // ignore
  }
}
