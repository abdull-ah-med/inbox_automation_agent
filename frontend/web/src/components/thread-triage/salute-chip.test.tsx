import { describe, expect, it } from "vitest"

import { chipForAddressee } from "@/components/thread-triage/salute-chip"
import type { ReplyAddresseeView } from "@/lib/types"

const base = (overrides: Partial<ReplyAddresseeView> = {}): ReplyAddresseeView => ({
  email: "dev@sample-site.example.com",
  salute_name: "team",
  source: "latest_inbound",
  source_kind: "team",
  directory_hit: false,
  ...overrides,
})

describe("chipForAddressee", () => {
  it("lets reviewers teach a Contact for role/team mailboxes", () => {
    const chip = chipForAddressee(base())
    expect(chip.label).toBe("team")
    expect(chip.disabled).toBe(false)
    expect(chip.mode).toBe("create")
    expect(chip.title.toLowerCase()).toMatch(/click|teach|override|alias/)
  })

  it("keeps directory aliases editable", () => {
    const chip = chipForAddressee(
      base({
        email: "samplecontact@example.com",
        salute_name: "Kelvin",
        source_kind: "directory",
        directory_hit: true,
      }),
    )
    expect(chip.disabled).toBe(false)
    expect(chip.mode).toBe("edit")
    expect(chip.showSavedDot).toBe(true)
  })
})
