import { act, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { StreamingEmailBody } from "@/components/streaming-email-body"

describe("StreamingEmailBody", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("does not dump a large chunk in a single paint while streaming", async () => {
    const full =
      "The overdue billing dispute is waiting on review for Bonnie Moore."
    render(<StreamingEmailBody text={full} streaming />)

    await act(async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve())
      })
    })

    expect(screen.queryByText(full)).not.toBeInTheDocument()
    expect(screen.getByLabelText("Generating answer")).toBeInTheDocument()
  })

  it("shows the full answer once streaming finishes and the reveal catches up", async () => {
    const full = "Caught up answer."
    const { rerender } = render(<StreamingEmailBody text={full} streaming />)
    rerender(<StreamingEmailBody text={full} streaming={false} />)

    expect(await screen.findByText(full)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByLabelText("Generating answer")).not.toBeInTheDocument()
    })
  })

  it("unwraps a wrapping markdown fence so the reviewer sees prose", () => {
    render(
      <StreamingEmailBody
        text={"```markdown\nBonnie is waiting on review.\n```"}
        streaming={false}
      />,
    )

    expect(screen.getByText("Bonnie is waiting on review.")).toBeInTheDocument()
    expect(screen.queryByText(/```/)).toBeNull()
  })
})
