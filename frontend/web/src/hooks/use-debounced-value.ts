import { useEffect, useState } from "react"

export const SEARCH_DEBOUNCE_MS = 300

export const useDebouncedValue = <T>(value: T, delayMs: number): T => {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    // Sync with an external timer — not derived state.
    // https://react.dev/learn/synchronizing-with-effects
    const timer = window.setTimeout(() => {
      setDebounced(value)
    }, delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])

  return debounced
}
