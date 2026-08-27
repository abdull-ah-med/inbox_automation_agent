import { describe, expect, it } from "vitest"

import { splitNestedQuotedSegments } from "@/lib/split-nested-quoted-segments"

describe("splitNestedQuotedSegments", () => {
  it("splits Outlook From/Sent layers so older mail is a deeper segment", () => {
    const quoted =
      "________________________________\n" +
      "From: Beau Norris <beau.norris@sample-lab-vendor.example.com>\n" +
      "Sent: Wednesday, August 26, 2026 4:49 PM\n" +
      "To: Elise Chouest <sampleagent@sample-site.example.com>\n" +
      "Subject: RE: Open Items\n\n" +
      "I just wanted to double check on definitions.\n\n" +
      "From: Elise Chouest <sampleagent@sample-site.example.com>\n" +
      "Sent: Wednesday, August 26, 2026 11:56 AM\n" +
      "To: Beau Norris <beau.norris@sample-lab-vendor.example.com>\n" +
      "Subject: Open Items\n\n" +
      "Hi Ruth and Jeremy,\n\n" +
      "We're working through our build spec.\n"

    const segments = splitNestedQuotedSegments(quoted)

    expect(segments).toHaveLength(2)
    expect(segments[0]).toContain("I just wanted to double check on definitions.")
    expect(segments[0]).toContain("From: Beau Norris")
    expect(segments[0]).not.toContain("We're working through our build spec.")
    expect(segments[1]).toContain("We're working through our build spec.")
    expect(segments[1]).toContain("From: Elise Chouest")
  })

  it("splits Zendesk prior comments at default-avatar boundaries", () => {
    const quoted =
      "[https://sample-helpdesk.example.com/images/2016/default-avatar-80.png]\n\n" +
      "info\n\n" +
      "Aug 25, 2026, 8:19 AM MDT\n\n" +
      "I updated the method for delivery last week.\n\n" +
      "[https://sample-helpdesk.example.com/system/photos/25898357572372/purple_flower_5.jpg]\n\n" +
      "Alex Taylor (SampleHelpdesk)\n\n" +
      "Aug 24, 2026, 9:00 AM MDT\n\n" +
      "Thanks for opening the ticket.\n"

    const segments = splitNestedQuotedSegments(quoted)

    expect(segments).toHaveLength(2)
    expect(segments[0]).toContain("I updated the method for delivery last week.")
    expect(segments[1]).toContain("Thanks for opening the ticket.")
  })

  it("returns a single segment when there is no nested boundary", () => {
    const segments = splitNestedQuotedSegments(
      "From: Alice <alice@example.com>\n" +
        "Sent: Monday, August 22, 2022 10:43 AM\n" +
        "To: sales@example.com\n" +
        "Subject: Re: Vercel deploy\n\n" +
        "Can you check the Vercel deploy?",
    )

    expect(segments).toHaveLength(1)
    expect(segments[0]).toContain("Can you check the Vercel deploy?")
  })
})
