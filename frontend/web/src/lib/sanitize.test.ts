import { describe, expect, it } from "vitest"

import { sanitizeUserText } from "@/lib/sanitize"

describe("sanitizeUserText", () => {
  it("strips HTML tags from search and chat input", () => {
    expect(sanitizeUserText("<b>SampleH</b>")).toBe("SampleH")
    expect(sanitizeUserText('<img src=x onerror="alert(1)">packet')).toBe("packet")
  })

  it("strips null bytes and collapses whitespace", () => {
    expect(sanitizeUserText("  SampleH\u0000  ")).toBe("SampleH")
    expect(sanitizeUserText("SampleH\n\n  order")).toBe("SampleH order")
  })
})
