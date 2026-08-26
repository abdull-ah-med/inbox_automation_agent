import { renderToString } from "react-dom/server"
import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { useIsClient } from "@/hooks/use-is-client"

const Probe = () => {
  const isClient = useIsClient()
  return <span>{isClient ? "client" : "server"}</span>
}

describe("useIsClient", () => {
  it("renders server during SSR so theme markup matches hydration", () => {
    const html = renderToString(<Probe />)
    expect(html).toContain("server")
    expect(html).not.toContain("client")
  })

  it("renders client in the browser without an effect", () => {
    render(<Probe />)
    expect(screen.getByText("client")).toBeInTheDocument()
  })
})
