import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { EmailBody } from "@/components/email-body"

describe("EmailBody", () => {
  it("renders markdown bold markers as emphasis without showing asterisks", () => {
    render(
      <EmailBody text={"1. **Check your spam/junk folder**\n2. Verify email"} />,
    )

    expect(screen.getByText("Check your spam/junk folder").tagName).toBe("STRONG")
    expect(screen.queryByText(/\*\*/)).toBeNull()
    expect(screen.getByText(/2\. Verify email/)).toBeInTheDocument()
  })

  it("still linkifies URLs inside and outside bold spans", () => {
    render(
      <EmailBody
        text={"Open **https://orders.sample-services.example.com/MyAppLogin.cfm** now"}
      />,
    )

    const link = screen.getByRole("link", {
      name: "https://orders.sample-services.example.com/MyAppLogin.cfm",
    })
    expect(link).toHaveAttribute(
      "href",
      "https://orders.sample-services.example.com/MyAppLogin.cfm",
    )
  })
})
