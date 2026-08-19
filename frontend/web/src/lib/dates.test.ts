import { afterEach, describe, expect, it, vi } from "vitest"

import {
  formatRelativeTime,
  formatReviewerDate,
  formatReviewerDateTime,
} from "@/lib/dates"

describe("reviewer dates", () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it("prints 8/10/2026 for Kelvin's August 10 example, not 10/8", () => {
    const text = formatReviewerDate("2026-08-10T14:00:00Z")
    expect(text).toContain("8/10/2026")
    expect(text).not.toMatch(/10\/8/)
  })

  it("prints a US datetime that still uses month-before-day", () => {
    const text = formatReviewerDateTime("2026-08-10T14:00:00Z")
    expect(text).toMatch(/8\/10\/2026/)
    expect(text).not.toMatch(/10\/8\/2026/)
  })

  it("uses US month-day for calendar fallback older than a week", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-08-18T14:00:00Z"))
    expect(formatRelativeTime("2026-08-10T14:00:00Z")).toBe("Aug 10")
  })
})
