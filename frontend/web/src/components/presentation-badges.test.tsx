import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { PresentationBadges } from "@/components/presentation-badges"

describe("PresentationBadges", () => {
  it("renders Now badges without Action needed when resolved finished story", () => {
    render(
      <PresentationBadges
        badges={[
          { kind: "state", label: "Resolved" },
          { kind: "internal", label: "Internal" },
        ]}
      />,
    )
    expect(screen.getByText("Resolved")).toBeInTheDocument()
    expect(screen.getByText("Internal")).toBeInTheDocument()
    expect(screen.queryByText("Action needed")).not.toBeInTheDocument()
  })
})
