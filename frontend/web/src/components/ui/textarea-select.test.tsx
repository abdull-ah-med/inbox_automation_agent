import { createRef } from "react"
import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { Textarea } from "@/components/ui/textarea"

describe("Textarea", () => {
  it("renders with aria-label and forwards ref", () => {
    const ref = createRef<HTMLTextAreaElement>()
    render(<Textarea ref={ref} aria-label="Notes" defaultValue="hello" />)
    const el = screen.getByLabelText("Notes")
    expect(el).toBeInTheDocument()
    expect(el).toHaveValue("hello")
    expect(ref.current).toBe(el)
  })

  it("applies disabled state", () => {
    render(<Textarea aria-label="Disabled notes" disabled />)
    expect(screen.getByLabelText("Disabled notes")).toBeDisabled()
  })
})
