import { createRef } from "react"
import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { Select } from "@/components/ui/select"
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

describe("Select", () => {
  it("renders options with aria-label and forwards ref", () => {
    const ref = createRef<HTMLSelectElement>()
    render(
      <Select ref={ref} aria-label="Priority" defaultValue="high">
        <option value="low">Low</option>
        <option value="high">High</option>
      </Select>,
    )
    const el = screen.getByLabelText("Priority")
    expect(el).toBeInTheDocument()
    expect(el).toHaveValue("high")
    expect(ref.current).toBe(el)
  })

  it("applies disabled state", () => {
    render(
      <Select aria-label="Disabled priority" disabled>
        <option value="low">Low</option>
      </Select>,
    )
    expect(screen.getByLabelText("Disabled priority")).toBeDisabled()
  })
})
