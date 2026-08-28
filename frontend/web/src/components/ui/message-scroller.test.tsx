import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"

describe("MessageScroller", () => {
  const originalScrollIntoView = Element.prototype.scrollIntoView

  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    Element.prototype.scrollIntoView = originalScrollIntoView
  })

  it("anchors new messages with instant scroll, not smooth animation", () => {
    render(
      <MessageScrollerProvider>
        <MessageScroller>
          <MessageScrollerViewport>
            <MessageScrollerContent>
              <MessageScrollerItem scrollAnchor>User question</MessageScrollerItem>
            </MessageScrollerContent>
          </MessageScrollerViewport>
        </MessageScroller>
      </MessageScrollerProvider>,
    )

    expect(Element.prototype.scrollIntoView).toHaveBeenCalled()
    const options = vi.mocked(Element.prototype.scrollIntoView).mock.calls[0]?.[0]
    expect(options).toEqual(
      expect.objectContaining({
        behavior: "auto",
      }),
    )
    expect((options as ScrollIntoViewOptions | undefined)?.behavior).not.toBe("smooth")
  })

  it("keeps stick-to-bottom instant when content grows while pinned", () => {
    const resizeCallbacks: Array<() => void> = []
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: () => void) {
          resizeCallbacks.push(callback)
        }
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )

    const { rerender } = render(
      <MessageScrollerProvider>
        <MessageScroller>
          <MessageScrollerViewport>
            <MessageScrollerContent>
              <p>short</p>
            </MessageScrollerContent>
          </MessageScrollerViewport>
        </MessageScroller>
      </MessageScrollerProvider>,
    )

    const viewport = screen.getByRole("log").parentElement
    expect(viewport).not.toBeNull()
    if (!viewport) return

    const scrollTo = vi.fn()
    Object.defineProperty(viewport, "scrollTo", {
      configurable: true,
      value: scrollTo,
    })
    Object.defineProperty(viewport, "scrollHeight", {
      configurable: true,
      get: () => 400,
    })
    Object.defineProperty(viewport, "clientHeight", {
      configurable: true,
      get: () => 200,
    })
    let scrollTop = 0
    Object.defineProperty(viewport, "scrollTop", {
      configurable: true,
      get: () => scrollTop,
      set: (value: number) => {
        scrollTop = value
      },
    })

    rerender(
      <MessageScrollerProvider>
        <MessageScroller>
          <MessageScrollerViewport>
            <MessageScrollerContent>
              <p>{"streamed answer ".repeat(40)}</p>
            </MessageScrollerContent>
          </MessageScrollerViewport>
        </MessageScroller>
      </MessageScrollerProvider>,
    )

    // Content growth is observed via ResizeObserver, not a no-deps layout effect.
    resizeCallbacks.forEach((callback) => callback())

    expect(scrollTop).toBe(400)
    expect(scrollTo).not.toHaveBeenCalled()
  })

  it("uses smooth scroll only when the reviewer clicks scroll to end", async () => {
    const user = userEvent.setup()
    render(
      <MessageScrollerProvider>
        <MessageScroller>
          <MessageScrollerViewport>
            <MessageScrollerContent>
              <p>Earlier</p>
            </MessageScrollerContent>
          </MessageScrollerViewport>
          <MessageScrollerButton />
        </MessageScroller>
      </MessageScrollerProvider>,
    )

    const viewport = screen.getByRole("log").parentElement
    expect(viewport).not.toBeNull()
    if (!viewport) return

    const scrollTo = vi.fn()
    Object.defineProperty(viewport, "scrollTo", {
      configurable: true,
      value: scrollTo,
    })
    Object.defineProperty(viewport, "scrollHeight", {
      configurable: true,
      get: () => 800,
    })
    Object.defineProperty(viewport, "clientHeight", {
      configurable: true,
      get: () => 200,
    })
    Object.defineProperty(viewport, "scrollTop", {
      configurable: true,
      get: () => 0,
      set: () => {},
    })

    // Pretend the reviewer scrolled away so the jump button is active.
    viewport.dispatchEvent(new Event("scroll"))
    const jump = await screen.findByRole("button", { name: /scroll to end/i })
    await user.click(jump)

    expect(scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({
        behavior: "smooth",
        top: 800,
      }),
    )
  })
})
