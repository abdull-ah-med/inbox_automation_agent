import { describe, expect, it } from "vitest"

import { looksLikeCourtesyClose } from "@/lib/courtesy-close"

describe("looksLikeCourtesyClose", () => {
  it("treats Olivia's courtesy close as not needing a reply", () => {
    expect(
      looksLikeCourtesyClose(
        "Sounds good, thanks! Let me know if you're unable to join Friday. Otherwise we are all set.",
      ),
    ).toBe(true)
  })

  it("keeps a real billing ask as needing a reply", () => {
    expect(
      looksLikeCourtesyClose(
        "Hi Elise — please process the samplelab invoice from SampleLabVendor and rebill Harmeyer Transport for last month.",
      ),
    ).toBe(false)
  })
})
