/**
 * Coalesces high-frequency stream deltas into one flush per animation frame
 * so React re-renders (and stick-to-bottom scroll) stay frame-aligned.
 */
export const createRafDeltaBatcher = (
  flush: (chunk: string) => void,
  schedule: typeof requestAnimationFrame = requestAnimationFrame,
  cancel: typeof cancelAnimationFrame = cancelAnimationFrame,
) => {
  let buffer = ""
  let handle: number | null = null

  const drain = (): string => {
    if (handle != null) {
      cancel(handle)
      handle = null
    }
    const chunk = buffer
    buffer = ""
    return chunk
  }

  const flushNow = () => {
    const chunk = drain()
    if (chunk) flush(chunk)
  }

  const push = (text: string) => {
    if (!text) return
    buffer += text
    if (handle != null) return
    handle = schedule(() => {
      handle = null
      const chunk = buffer
      buffer = ""
      if (chunk) flush(chunk)
    })
  }

  return { push, flushNow, drain }
}
