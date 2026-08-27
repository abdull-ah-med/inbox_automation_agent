import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { Tabs, TabsContent, TabsIndicator, TabsList, TabsTrigger } from "@/components/ui/tabs"

describe("Tabs sliding control", () => {
  it("renders a pill track with a sliding indicator behind the active tab", async () => {
    const user = userEvent.setup()
    render(
      <Tabs defaultValue="classification">
        <TabsList aria-label="Thread review sections">
          <TabsTrigger value="classification">Classification</TabsTrigger>
          <TabsTrigger value="draft">Draft</TabsTrigger>
          <TabsTrigger value="audit">Audit (17)</TabsTrigger>
          <TabsIndicator />
        </TabsList>
        <TabsContent value="classification">Class body</TabsContent>
        <TabsContent value="draft">Draft body</TabsContent>
        <TabsContent value="audit">Audit body</TabsContent>
      </Tabs>,
    )

    const list = screen.getByRole("tablist", { name: "Thread review sections" })
    expect(list.className).toMatch(/rounded-full|rounded-xl/)
    expect(list.className).toContain("bg-muted")

    const indicator = list.querySelector("[data-slot='tabs-indicator']")
    expect(indicator).not.toBeNull()
    expect(indicator?.className).toContain("bg-background")
    expect(indicator?.className).toMatch(/shadow/)

    const audit = screen.getByRole("tab", { name: "Audit (17)" })
    await user.click(audit)
    expect(audit).toHaveAttribute("data-active")
    expect(screen.getByText("Audit body")).toBeInTheDocument()
  })
})
