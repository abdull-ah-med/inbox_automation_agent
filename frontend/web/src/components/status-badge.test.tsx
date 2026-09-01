import { describe, expect, it } from "vitest"

import { stateTone, stateToneFromLabel } from "@/components/status-badge"

describe("stateTone", () => {
  it("gives Draft ready and Resolved different tones", () => {
    expect(stateTone("DRAFTED")).toBe("green")
    expect(stateTone("RESOLVED")).toBe("blue")
    expect(stateTone("DRAFTED")).not.toBe(stateTone("RESOLVED"))
  })

  it("maps presentation labels Drafted and Resolved to the same distinct tones", () => {
    expect(stateToneFromLabel("Drafted")).toBe("green")
    expect(stateToneFromLabel("Resolved")).toBe("blue")
    expect(stateToneFromLabel("Drafted")).not.toBe(stateToneFromLabel("Resolved"))
  })
})
