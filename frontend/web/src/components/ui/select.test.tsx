import { useState } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const items = [
  { label: "Low", value: "low" },
  { label: "High", value: "high" },
]

const PrioritySelect = ({ disabled = false }: { disabled?: boolean }) => {
  const [value, setValue] = useState("low")
  return (
    <Select
      items={items}
      value={value}
      onValueChange={(next) => setValue(next ?? "low")}
      disabled={disabled}
    >
      <SelectTrigger aria-label={disabled ? "Disabled priority" : "Priority"} size="sm">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

describe("Select", () => {
  it("uses the same outline button chrome as filter buttons", () => {
    render(
      <>
        <Button variant="outline" size="sm">
          Stale only
        </Button>
        <PrioritySelect />
      </>,
    )
    const button = screen.getByRole("button", { name: "Stale only" })
    const trigger = screen.getByRole("combobox", { name: "Priority" })
    expect(trigger.tagName).toBe("BUTTON")
    expect(trigger.className).toContain("border-border")
    expect(button.className).toContain("border-border")
    expect(trigger.className).toContain("bg-background")
    expect(button.className).toContain("bg-background")
  })

  it("opens the menu below the trigger instead of covering it", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<PrioritySelect />)
    await user.click(screen.getByRole("combobox", { name: "Priority" }))
    const listbox = await screen.findByRole("listbox")
    expect(listbox.closest("[data-slot='select-content']")).toHaveAttribute(
      "data-align-trigger",
      "false",
    )
    expect(screen.getByRole("combobox", { name: "Priority" })).toBeVisible()
  })

  it("lets the user pick an option from the menu", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<PrioritySelect />)
    await user.click(screen.getByRole("combobox", { name: "Priority" }))
    await user.click(await screen.findByRole("option", { name: "High" }))
    expect(screen.getByRole("combobox", { name: "Priority" })).toHaveTextContent("High")
  })

  it("applies disabled state on the trigger", () => {
    render(<PrioritySelect disabled />)
    expect(screen.getByRole("combobox", { name: "Disabled priority" })).toBeDisabled()
  })
})
