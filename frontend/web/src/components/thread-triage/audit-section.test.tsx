import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { AuditSection } from "@/components/thread-triage/audit-section"
import type { AuditEntry } from "@/lib/types"
import { renderWithProviders } from "@/test/render"

const entry = (
  overrides: Pick<AuditEntry, "timestamp" | "event"> & Partial<AuditEntry>,
): AuditEntry => ({
  detail: "",
  source: "pipeline",
  ...overrides,
})

describe("AuditSection", () => {
  it("lists the newest event first even when the log arrives oldest first", () => {
    renderWithProviders(
      <AuditSection
        auditLog={[
          entry({ timestamp: "2026-08-01T12:00:00Z", event: "draft.generated" }),
          entry({ timestamp: "2026-08-03T12:00:00Z", event: "draft.approved" }),
          entry({ timestamp: "2026-08-02T12:00:00Z", event: "triage.action_needed" }),
        ]}
      />,
    )

    const items = screen.getAllByRole("listitem").map((item) => item.textContent ?? "")
    expect(items[0]).toContain("Draft Approved")
    expect(items[1]).toContain("Triage Action Needed")
    expect(items[2]).toContain("Draft Generated")
  })

  it("folds events older than the latest seven behind a show control", async () => {
    const user = userEvent.setup()
    const auditLog = Array.from({ length: 8 }, (_, index) =>
      entry({
        timestamp: `2026-08-0${index + 1}T12:00:00Z`,
        event: `event.${index}`,
        detail: `What happened in event ${index}`,
      }),
    )

    renderWithProviders(<AuditSection auditLog={auditLog} />)

    expect(screen.queryByText("What happened in event 0")).not.toBeInTheDocument()
    expect(screen.getByText("What happened in event 1")).toBeInTheDocument()
    expect(screen.getByText("What happened in event 7")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Show 1 earlier event" }))
    expect(screen.getByText("What happened in event 0")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Hide earlier events" }))
    expect(screen.queryByText("What happened in event 0")).not.toBeInTheDocument()
  })
})
