import { describe, expect, it } from "vitest"

import {
  mailboxListReturnTo,
  parseMailboxReturnTo,
  threadHrefWithMailboxReturn,
} from "@/lib/mailbox-list-return"

describe("mailboxListReturnTo", () => {
  it("includes the filter query when present", () => {
    expect(mailboxListReturnTo("/mailboxes/sales", "state=REPLY_REVIEW&urgency=HIGH")).toBe(
      "/mailboxes/sales?state=REPLY_REVIEW&urgency=HIGH",
    )
  })

  it("returns pathname only when filters are cleared", () => {
    expect(mailboxListReturnTo("/mailboxes/sales", "")).toBe("/mailboxes/sales")
  })
})

describe("parseMailboxReturnTo", () => {
  it("accepts mailbox list paths with filters", () => {
    expect(parseMailboxReturnTo("/mailboxes/sales?state=STALE")).toBe(
      "/mailboxes/sales?state=STALE",
    )
  })

  it("rejects external URLs", () => {
    expect(parseMailboxReturnTo("https://evil.example/mailboxes/sales")).toBeNull()
    expect(parseMailboxReturnTo("//evil.example/mailboxes/sales")).toBeNull()
  })

  it("rejects non-mailbox paths", () => {
    expect(parseMailboxReturnTo("/dashboard")).toBeNull()
  })
})

describe("threadHrefWithMailboxReturn", () => {
  it("embeds returnTo for the thread detail breadcrumb", () => {
    expect(threadHrefWithMailboxReturn("thread-1", "/mailboxes/sales?state=REPLY_REVIEW")).toBe(
      "/threads/thread-1?returnTo=%2Fmailboxes%2Fsales%3Fstate%3DREPLY_REVIEW",
    )
  })

  it("omits returnTo when not coming from a filtered list", () => {
    expect(threadHrefWithMailboxReturn("thread-1")).toBe("/threads/thread-1")
  })
})
