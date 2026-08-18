import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { SEARCH_DEBOUNCE_MS, useDebouncedValue } from "@/hooks/use-debounced-value"

describe("useDebouncedValue", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("keeps the previous value until the delay elapses", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebouncedValue(value, SEARCH_DEBOUNCE_MS),
      { initialProps: { value: "D" } },
    )
    expect(result.current).toBe("D")
    rerender({ value: "Di" })
    expect(result.current).toBe("D")
    act(() => {
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS - 1)
    })
    expect(result.current).toBe("D")
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current).toBe("Di")
  })
})
