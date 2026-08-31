import { describe, expect, it } from "vitest"

import { stripPlainTextArtifacts } from "@/lib/strip-plain-text-artifacts"

describe("stripPlainTextArtifacts", () => {
  it("unmangles nested Graph mailto angle junk to the visible address", () => {
    const cleaned = stripPlainTextArtifacts(
      "E: info@sample-services.example.com" +
        "<mailto:info@sample-services.example.com>" +
        "<mailto:info@sample-services.example.com<mailto:info@sample-services.example.com>>",
    )

    expect(cleaned).toBe("E: info@sample-services.example.com")
    expect(cleaned).not.toMatch(/mailto:/i)
  })

  it("drops cid image placeholders and keeps the surrounding prose", () => {
    const cleaned = stripPlainTextArtifacts(
      "Thanks,\nElise\n\n[cid:image004.png@01DD3549.0D900550]\n\nwww.sample-site.example.com",
    )

    expect(cleaned).toContain("Thanks,")
    expect(cleaned).toContain("www.sample-site.example.com")
    expect(cleaned).not.toMatch(/\[cid:/i)
  })
})
