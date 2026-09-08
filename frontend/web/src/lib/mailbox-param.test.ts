import { describe, expect, it } from "vitest"

import { decodeMailboxParam } from "@/lib/mailbox-param"

describe("decodeMailboxParam", () => {
  it("decodes a mailbox key from the route", () => {
    expect(decodeMailboxParam("client-relations")).toBe("client-relations")
    expect(decodeMailboxParam("sales%40example.com")).toBe("sales@example.com")
  })

  it("returns null for a malformed percent-encoding", () => {
    expect(decodeMailboxParam("%E0%A4%A")).toBeNull()
  })
})
