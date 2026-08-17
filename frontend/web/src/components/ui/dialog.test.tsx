import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const renderDialog = (size?: "sm" | "md" | "lg" | "xl" | "2xl") => {
  return render(
    <Dialog open>
      <DialogContent size={size} aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Test dialog</DialogTitle>
          <DialogDescription>Dialog body</DialogDescription>
        </DialogHeader>
      </DialogContent>
    </Dialog>,
  )
}

describe("DialogContent size variants", () => {
  it("defaults to sm max-width with scroll constraints", () => {
    renderDialog()
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("data-size", "sm")
    expect(dialog.className).toContain("sm:max-w-sm")
    expect(dialog.className).toContain("max-h-[85vh]")
    expect(dialog.className).toContain("overflow-y-auto")
  })

  it("applies md size class", () => {
    renderDialog("md")
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("data-size", "md")
    expect(dialog.className).toContain("sm:max-w-lg")
  })

  it("applies lg size class", () => {
    renderDialog("lg")
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("data-size", "lg")
    expect(dialog.className).toContain("sm:max-w-2xl")
  })

  it("applies xl size class", () => {
    renderDialog("xl")
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("data-size", "xl")
    expect(dialog.className).toContain("sm:max-w-3xl")
  })

  it("caps 2xl at 72rem on xl screens and keeps 2rem gutters below that", () => {
    renderDialog("2xl")
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("data-size", "2xl")
    expect(dialog.className).toContain("xl:max-w-[min(72rem,calc(100%-2rem))]")
    expect(dialog.className).toContain("lg:max-w-[min(64rem,calc(100%-2rem))]")
    expect(dialog.className).toContain("max-w-[calc(100%-2rem)]")
  })
})
