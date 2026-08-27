import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { HtmlEmailFrame, wrapEmailHtml } from "@/components/html-email-frame"

describe("wrapEmailHtml", () => {
  it("embeds Outlook-like reading pane chrome and CSP that blocks scripts", () => {
    const wrapped = wrapEmailHtml("<b>Invoice</b>")
    expect(wrapped).toContain("<b>Invoice</b>")
    expect(wrapped).toContain("script-src 'none'")
    expect(wrapped).toContain("img-src https: data:")
    expect(wrapped).toContain('target="_blank"')
    expect(wrapped).toContain('"Segoe UI"')
    expect(wrapped).toContain("background:#fff")
  })
})

describe("HtmlEmailFrame", () => {
  it("renders a sandboxed iframe without allow-scripts", () => {
    render(<HtmlEmailFrame html="<p>Hello invoice</p>" title="Email from alice" />)
    const frame = screen.getByTitle("Email from alice")
    expect(frame.tagName).toBe("IFRAME")
    const sandbox = frame.getAttribute("sandbox") ?? ""
    expect(sandbox).toContain("allow-popups")
    expect(sandbox).toContain("allow-same-origin")
    expect(sandbox).not.toContain("allow-scripts")
    expect(frame.getAttribute("srcdoc") ?? "").toContain("<p>Hello invoice</p>")
  })
})
