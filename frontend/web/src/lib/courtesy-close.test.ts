import { describe, expect, it } from "vitest"

import { looksLikeCourtesyClose } from "@/lib/courtesy-close"

const METHOD_PS_FOLLOW_UP =
  "Hi Elise and Brad,\n" +
  "\n" +
  "I wanted to follow up on my previous email regarding the payment sync " +
  "issues and the complimentary hour of Professional Services time we " +
  "offered to help review your customized screens.\n" +
  "\n" +
  "Please let me know if you would like to proceed with this, and I will " +
  "be happy to coordinate the next steps with our team.\n" +
  "\n" +
  "Best regards,\n" +
  "\n" +
  "On Thu, Aug 20, 2026 at 11:43 AM Smit Patel <smit.patel@sample-transport.example.com> wrote:\n" +
  "\n" +
  "Thank you again for taking the time to speak with me regarding the " +
  "payment sync issues.\n"

const COURTESY_ABOVE_QUOTED_ASK =
  "Sounds good, thanks! We are all set.\n" +
  "\n" +
  "On Thu, Aug 20, 2026 at 11:43 AM Smit Patel <smit.patel@sample-transport.example.com> wrote:\n" +
  "\n" +
  "Can you please send the onboarding packet?\n"

describe("looksLikeCourtesyClose", () => {
  it("treats Olivia's courtesy close as not needing a reply", () => {
    expect(
      looksLikeCourtesyClose(
        "Sounds good, thanks! Let me know if you're unable to join Friday. Otherwise we are all set.",
      ),
    ).toBe(true)
  })

  it("treats a thank-you-only reply as a courtesy close", () => {
    expect(looksLikeCourtesyClose("Thank you!")).toBe(true)
  })

  it("keeps a real billing ask as needing a reply", () => {
    expect(
      looksLikeCourtesyClose(
        "Hi Elise — please process the samplelab invoice from SampleLabVendor and rebill Harmeyer Transport for last month.",
      ),
    ).toBe(false)
  })

  it("ignores a quoted thank-you under a follow-up that asks to proceed", () => {
    expect(looksLikeCourtesyClose(METHOD_PS_FOLLOW_UP)).toBe(false)
  })

  it("still treats a courtesy close as a close when the quote below is an ask", () => {
    expect(looksLikeCourtesyClose(COURTESY_ABOVE_QUOTED_ASK)).toBe(true)
  })

  it("does not treat a follow-up that ends with Thank you as a courtesy close", () => {
    expect(
      looksLikeCourtesyClose(
        "Hi Elise and Jodi,\n" +
          "Just following up on Weeks 3 and 4 of the SampleSite content calendar\n" +
          "No rush, but if possible, it would be great to have your approval by the " +
          "end of the week so we can keep things moving\n" +
          "Thank you!\n" +
          "\n" +
          "Karol Duarte\n" +
          "Digital Marketing Coordinator\n" +
          "\n" +
          "________________________________\n" +
          "De: Karol Duarte\n" +
          "Enviado: martes, 18 de agosto de 2026 15:23\n" +
          "Asunto: Content Calendar - (FB-IG-LK)\n" +
          "\n" +
          "Hi Elise and Jodi,\n" +
          "I’m sharing Weeks 3 and 4 of the SampleSite content calendar for your review\n" +
          "Thank you!\n",
      ),
    ).toBe(false)
  })

  it("does not treat Jennifer's Bonita Springs check ask as a courtesy close", () => {
    expect(
      looksLikeCourtesyClose(
        "Do all the checks go to Bonita Springs? We are looking for the payment " +
          "for Trinity Electrical. It looks as though it is a check for $65? " +
          "Thank you for any advice on this.\n" +
          "\n" +
          "Jennifer Chance\n" +
          "SamplSampleLabing, LLC\n",
      ),
    ).toBe(false)
  })
})
