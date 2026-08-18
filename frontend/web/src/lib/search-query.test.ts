import { describe, expect, it } from "vitest"

import {
  insertFilterKey,
  isSearchableQuery,
  matchingFilterKeys,
  parseSearchQuery,
  SEARCH_FILTER_KEYS,
  completePendingFilter,
  filterValueSuggestions,
  filterValueEntry,
} from "@/lib/search-query"

describe("parseSearchQuery", () => {
  it("stacks from, subject, mailbox, and leftover keywords", () => {
    const parsed = parseSearchQuery(
      "from:vendor@example.com subject:packet mailbox:sales overdue",
    )
    expect(parsed.senders).toEqual(["vendor@example.com"])
    expect(parsed.subjects).toEqual(["packet"])
    expect(parsed.mailboxes).toEqual(["sales"])
    expect(parsed.freeText).toBe("overdue")
  })

  it("keeps quoted filter values", () => {
    const parsed = parseSearchQuery(
      'from:"Elise Kelvin" contains:"drug screen" invoice',
    )
    expect(parsed.senders).toEqual(["Elise Kelvin"])
    expect(parsed.contains).toEqual(["drug screen"])
    expect(parsed.freeText).toBe("invoice")
  })

  it("lists the Discord-style operators users can stack", () => {
    expect(SEARCH_FILTER_KEYS).toEqual([
      "from",
      "contains",
      "subject",
      "direction",
      "mailbox",
    ])
  })

  it("treats a space after from: as part of the operator", () => {
    const parsed = parseSearchQuery("from: vendor@example.com packet")
    expect(parsed.senders).toEqual(["vendor@example.com"])
    expect(parsed.freeText).toBe("packet")
  })

  it("does not treat a bare from: as a keyword search", () => {
    const parsed = parseSearchQuery("from:")
    expect(parsed.senders).toEqual([])
    expect(parsed.freeText).toBe("")
    expect(isSearchableQuery("from:")).toBe(false)
    expect(isSearchableQuery("from:vendor@example.com")).toBe(true)
    expect(isSearchableQuery("packet")).toBe(true)
  })

  it("hides other operators while from: is waiting for a sender", () => {
    expect(matchingFilterKeys("from:")).toEqual([])
    expect(matchingFilterKeys("from: ")).toEqual([])
    expect(matchingFilterKeys("from:vendor@example.com ")).toEqual([
      ...SEARCH_FILTER_KEYS,
    ])
  })

  it("appends from: after a keyword instead of replacing it", () => {
    expect(insertFilterKey("packet", "from")).toBe("packet from:")
    expect(insertFilterKey("fr", "from")).toBe("from:")
    expect(insertFilterKey("", "from")).toBe("from:")
  })

  it("completes a dangling filter with a suggested value", () => {
    expect(completePendingFilter("mailbox:", "sales")).toBe("mailbox:sales ")
    expect(completePendingFilter("direction:", "inbound")).toBe(
      "direction:inbound ",
    )
  })

  it("reads the active direction/mailbox value being typed", () => {
    expect(filterValueEntry("mailbox:")).toEqual({ key: "mailbox", prefix: "" })
    expect(filterValueEntry("mailbox:sa")).toEqual({
      key: "mailbox",
      prefix: "sa",
    })
    expect(filterValueEntry("from:vendor")).toBeNull()
  })

  it("filters mailbox and direction value suggestions by the typed prefix", () => {
    expect(filterValueSuggestions("direction", ["inbound", "outbound"], "")).toEqual([
      "inbound",
      "outbound",
    ])
    expect(
      filterValueSuggestions("direction", ["inbound", "outbound"], "in"),
    ).toEqual(["inbound"])
    expect(
      filterValueSuggestions("mailbox", ["sales", "vendor", "client-relations"], "sa"),
    ).toEqual(["sales"])
  })
})
