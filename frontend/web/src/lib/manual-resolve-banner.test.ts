import { describe, expect, it } from "vitest"

import { formatManualResolveLines } from "@/lib/manual-resolve-banner"

describe("formatManualResolveLines", () => {
  it("splits a full manual-resolve audit body without repeating the header", () => {
    const lines = formatManualResolveLines(
      "You marked this thread resolved. Removed from Needs Attention. Assessed urgency was HIGH; it no longer drives priority. Actions taken: normal gmail alert",
      null,
    )

    expect(lines).toEqual([
      "Removed from Needs Attention.",
      "Assessed urgency was HIGH; it no longer drives priority.",
      "Actions taken: normal gmail alert",
      "You can reopen if work is still open.",
    ])
  })

  it("falls back to urgencyAssessed when audit body is missing", () => {
    expect(formatManualResolveLines(null, "LOW")).toEqual([
      "Removed from Needs Attention.",
      "Assessed urgency was LOW; it no longer drives priority.",
      "You can reopen if work is still open.",
    ])
  })
})
