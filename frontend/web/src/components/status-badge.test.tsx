import { describe, expect, it } from "vitest"

import {
  dispositionLabel,
  dispositionTone,
  stateTone,
  stateToneFromLabel,
} from "@/components/status-badge"

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

describe("dispositionTone and dispositionLabel", () => {
  it("maps each disposition to the Elise-facing badge copy", () => {
    expect(dispositionLabel("reply_review")).toBe("Reply ready")
    expect(dispositionLabel("action_no_draft")).toBe("Action needed")
    expect(dispositionLabel("fyi_briefing")).toBe("FYI")
    expect(dispositionLabel("waiting_on_them")).toBe("Waiting on them")
    expect(dispositionLabel("needs_human")).toBe("Needs human")
    expect(dispositionLabel("processing")).toBe("Processing")
    expect(dispositionLabel("resolved_draftassistant")).toBe("Resolved by DraftAssistant")
    expect(dispositionLabel("resolved_elise")).toBe("Resolved by Elise")
    expect(dispositionLabel("spam")).toBe("Spam")
  })

  it("never labels a disposition as Drafted", () => {
    const kinds = [
      "reply_review",
      "action_no_draft",
      "fyi_briefing",
      "waiting_on_them",
      "needs_human",
      "processing",
      "resolved_draftassistant",
      "resolved_elise",
      "spam",
    ]
    for (const kind of kinds) {
      expect(dispositionLabel(kind).toLowerCase()).not.toContain("drafted")
    }
  })

  it("uses distinct tones for DraftAssistant, Elise, reply, action, and FYI", () => {
    expect(dispositionTone("resolved_draftassistant")).toBe("green")
    expect(dispositionTone("resolved_elise")).toBe("teal")
    expect(dispositionTone("reply_review")).toBe("green")
    expect(dispositionTone("action_no_draft")).toBe("amber")
    expect(dispositionTone("fyi_briefing")).toBe("neutral")
    expect(dispositionTone("waiting_on_them")).toBe("blue")
    expect(dispositionTone("needs_human")).toBe("red")
    expect(dispositionTone("processing")).toBe("neutral")
    expect(dispositionTone("spam")).toBe("orange")
  })
})
