/**
 * Auth redirects that must not leave a recoverable history entry
 * (back-button / bfcache) to the previous screen.
 */

const LOGOUT_GUARD_KEY = "itr_logged_out"

export const setLogoutGuard = (): void => {
  if (typeof window === "undefined") return
  sessionStorage.setItem(LOGOUT_GUARD_KEY, "1")
}

export const clearLogoutGuard = (): void => {
  if (typeof window === "undefined") return
  sessionStorage.removeItem(LOGOUT_GUARD_KEY)
}

export const hasLogoutGuard = (): boolean => {
  if (typeof window === "undefined") return false
  return sessionStorage.getItem(LOGOUT_GUARD_KEY) === "1"
}

/** Replace the current history entry with the login page (no back to prior screen). */
export const replaceToLogin = (nextPath?: string): void => {
  setLogoutGuard()
  const next = nextPath && nextPath !== "/login" ? nextPath : ""
  const url = next ? `/login?next=${encodeURIComponent(next)}` : "/login"
  window.location.replace(url)
}

/** After sign-in — replace so Back cannot return to /login. */
export const replaceAfterLogin = (destination: string): void => {
  clearLogoutGuard()
  const path =
    destination.startsWith("/") && !destination.startsWith("//") && !destination.startsWith("/\\")
      ? destination
      : "/dashboard"
  window.location.replace(path)
}
