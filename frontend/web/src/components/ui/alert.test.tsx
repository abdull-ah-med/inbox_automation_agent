import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

describe("Alert", () => {
  it("keeps title and description in the content column when there is no icon", () => {
    render(
      <Alert variant="destructive">
        <AlertTitle>Could not ask InboxAssistant</AlertTitle>
        <AlertDescription>
          The server is temporarily unreachable. Please try again shortly.
        </AlertDescription>
      </Alert>,
    )
    expect(screen.getByText("Could not ask InboxAssistant").className).toMatch(/col-start-2/)
    expect(screen.getByText(/the server is temporarily unreachable/i).className).toMatch(
      /col-start-2/,
    )
  })
})
