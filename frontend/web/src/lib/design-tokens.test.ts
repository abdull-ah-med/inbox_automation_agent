import { describe, expect, it } from "vitest"

import { inboxAccentStyle, inboxChipClassName, inboxColor } from "@/lib/design-tokens"

describe("inboxAccentStyle / inboxChipClassName", () => {
  it("exposes the mailbox accent as a CSS variable for themed chips", () => {
    expect(inboxAccentStyle("sales")).toEqual({
      "--inbox-accent": "#16a34a",
    })
    expect(inboxAccentStyle("vendor")).toEqual({
      "--inbox-accent": inboxColor("vendor"),
    })
  })

  it("uses a light tinted chip and keeps dark mode solid fill with white text", () => {
    expect(inboxChipClassName).toContain("color-mix")
    expect(inboxChipClassName).toContain("dark:bg-[var(--inbox-accent)]")
    expect(inboxChipClassName).toContain("dark:text-white")
  })
})
