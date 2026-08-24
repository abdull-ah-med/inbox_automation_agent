import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { CourtesyCloseBanner } from "@/components/courtesy-close-banner"

const OLIVIA =
  "Sounds good, thanks! Let me know if you're unable to join Friday. Otherwise we are all set."
const BILLING =
  "Hi Elise — please process the samplelab invoice from SampleLabVendor and rebill Harmeyer Transport for last month."

describe("CourtesyCloseBanner", () => {
  it("shows the no-reply hint for a drafted courtesy close", () => {
    render(<CourtesyCloseBanner state="DRAFTED" lastInboundBody={OLIVIA} />)
    expect(screen.getByRole("status")).toHaveTextContent(
      "Courtesy close. No reply needed. Click Reject, then choose Wrong action / no reply needed to mark this done and remove it from Needs Attention.",
    )
  })

  it("renders nothing for a billing ask still in draft", () => {
    render(<CourtesyCloseBanner state="DRAFTED" lastInboundBody={BILLING} />)
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("renders nothing once the thread is no longer drafted", () => {
    render(<CourtesyCloseBanner state="NO_ACTION" lastInboundBody={OLIVIA} />)
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("renders nothing when a quoted thank-you sits under a new follow-up", () => {
    render(
      <CourtesyCloseBanner
        state="DRAFTED"
        lastInboundBody={
          "Please let me know if you would like to proceed with this.\n\n" +
          "On Thu, Aug 20, 2026 at 11:43 AM Smit Patel <smit.patel@sample-transport.example.com> wrote:\n\n" +
          "Thank you again for taking the time to speak with me.\n"
        }
      />,
    )
    expect(screen.queryByRole("status")).toBeNull()
  })
})
