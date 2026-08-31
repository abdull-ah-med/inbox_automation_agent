import { describe, expect, it } from "vitest"

import { addCalendarDays, formatYmdLocal, parseYmdLocal } from "@/lib/calendar-dates"

describe("calendar-dates", () => {
  it("round-trips local calendar dates", () => {
    const date = parseYmdLocal("2026-08-28")
    expect(formatYmdLocal(date)).toBe("2026-08-28")
  })

  it("adds calendar days in YMD space", () => {
    expect(addCalendarDays("2026-08-01", 7)).toBe("2026-08-08")
  })
})
