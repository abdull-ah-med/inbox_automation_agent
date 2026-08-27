import { describe, expect, it } from "vitest"

import { ApiError } from "@/lib/api-client"
import { friendlyErrorFromUnknown, getErrorMessage, messageForStatus } from "@/lib/error-messages"

describe("error-messages", () => {
  it("maps 429 rate limiting to a clear retry message", () => {
    expect(messageForStatus(429)).toBe(
      "You have tried too many times. Please try again after some time.",
    )
    expect(getErrorMessage(new ApiError("ignored", 429))).toBe(
      "You have tried too many times. Please try again after some time.",
    )
  })

  it("maps common auth and server statuses", () => {
    expect(messageForStatus(401)).toMatch(/sign in/i)
    expect(messageForStatus(403)).toMatch(/permission/i)
    expect(messageForStatus(404)).toMatch(/could not find/i)
    expect(messageForStatus(500)).toMatch(/unexpected error/i)
  })

  it("does not expose bare status codes as the user message", () => {
    const msg = messageForStatus(429)
    expect(msg).not.toMatch(/\b429\b/)
    expect(getErrorMessage(new Error("Request failed (429)"))).not.toMatch(/\b429\b/)
  })

  it("maps network failures", () => {
    expect(getErrorMessage(new TypeError("Failed to fetch"))).toMatch(/connection/i)
  })

  it("maps server-component style errors to a safe message", () => {
    const err = new Error("An error occurred in the Server Components render.") as Error & {
      digest?: string
    }
    err.digest = "abc123"
    const friendly = friendlyErrorFromUnknown(err)
    expect(friendly.description).not.toMatch(/Server Components/)
    expect(friendly.title).toBe("Something went wrong")
  })
})
