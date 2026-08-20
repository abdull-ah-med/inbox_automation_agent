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

  it("turns [n] markers into links to the matching citation thread", () => {
    render(
      <EmailBody
        text="Bonnie is still waiting on the overdue invoice [1]."
        citations={[
          {
            thread_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            mailbox: "sales@example.com",
            subject: "Invoice dispute — overdue billing",
            state: "REQUIRES_HUMAN",
            urgency: "HIGH",
            url_path: "/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          },
        ]}
      />,
    )

    const link = screen.getByRole("link", { name: /citation 1/i })
    expect(link).toHaveAttribute(
      "href",
      "/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    expect(link).toHaveAccessibleName(/invoice dispute/i)
    expect(screen.getByText(/Bonnie is still waiting/)).toBeInTheDocument()
  })

  it("leaves unknown [n] markers as plain text when no citation matches", () => {
    render(<EmailBody text="Orphan marker [9] stays plain." citations={[]} />)
    expect(screen.queryByRole("link", { name: /citation 9/i })).toBeNull()
    expect(screen.getByText(/\[9\]/)).toBeInTheDocument()
  })
})
