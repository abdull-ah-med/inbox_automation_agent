import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { EmailBody, splitQuotedHistory } from "@/components/email-body"

const QUOTE_ONLY =
  "From: Alice <alice@example.com>\n" +
  "Sent: Monday, August 22, 2022 10:43 AM\n" +
  "To: sales@example.com\n" +
  "Subject: Re: Vercel deploy\n\n" +
  "Can you check the Vercel deploy?"

describe("splitQuotedHistory", () => {
  it("keeps an Outlook quote-only body collapsed with an empty unique reply", () => {
    const { main, quoted } = splitQuotedHistory(QUOTE_ONLY)

    expect(main).toBe("")
    expect(quoted).toContain("From: Alice")
    expect(quoted).toContain("Can you check the Vercel deploy?")
  })

  it("normalizes CRLF before matching Outlook headers", () => {
    const crlf = QUOTE_ONLY.replaceAll("\n", "\r\n")
    const { main, quoted } = splitQuotedHistory(crlf)

    expect(main).toBe("")
    expect(quoted).toContain("From: Alice")
  })
})

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

  it("shows No new text in this reply instead of the From/Sent wall", async () => {
    const user = userEvent.setup()
    render(<EmailBody text={QUOTE_ONLY} collapseQuotes />)

    expect(screen.getByText("No new text in this reply")).toBeInTheDocument()
    expect(screen.queryByText(/From: Alice/)).not.toBeInTheDocument()

    await user.click(
      screen.getByRole("button", { name: "Show quoted earlier messages" }),
    )
    expect(screen.getByText(/From: Alice/)).toBeInTheDocument()
  })

  it("indents expanded quoted history so earlier mail reads as nested", async () => {
    const user = userEvent.setup()
    render(
      <EmailBody
        text={"Thanks — please proceed.\n\n" + QUOTE_ONLY}
        collapseQuotes
      />,
    )

    await user.click(
      screen.getByRole("button", { name: "Show quoted earlier messages" }),
    )

    const quoted = screen.getByText(/From: Alice/).closest("[data-quoted-history]")
    expect(quoted).not.toBeNull()
    expect(quoted?.className).toMatch(/border-l/)
    expect(quoted?.className).toMatch(/pl-/)
  })
})
