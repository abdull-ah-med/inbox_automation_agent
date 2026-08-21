"use client"

import * as React from "react"
import { ArrowDownIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type MessageScrollerContextValue = {
  viewportRef: React.RefObject<HTMLDivElement | null>
  pinnedToEnd: boolean
  handleScroll: () => void
  scrollToEnd: () => void
}

const MessageScrollerContext =
  React.createContext<MessageScrollerContextValue | null>(null)

const useMessageScroller = () => {
  const value = React.useContext(MessageScrollerContext)
  if (!value) {
    throw new Error("MessageScroller components must be used within MessageScrollerProvider")
  }
  return value
}

function MessageScrollerProvider({ children }: { children: React.ReactNode }) {
  const viewportRef = React.useRef<HTMLDivElement | null>(null)
  const [pinnedToEnd, setPinnedToEnd] = React.useState(true)

  const handleScroll = React.useCallback(() => {
    const node = viewportRef.current
    if (!node) return
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight
    setPinnedToEnd(distance < 48)
  }, [])

  const scrollToEnd = React.useCallback(() => {
    const node = viewportRef.current
    if (!node) return
    if (typeof node.scrollTo === "function") {
      node.scrollTo({ top: node.scrollHeight, behavior: "smooth" })
    } else {
      node.scrollTop = node.scrollHeight
    }
    setPinnedToEnd(true)
  }, [])

  const value = React.useMemo(
    () => ({ viewportRef, pinnedToEnd, handleScroll, scrollToEnd }),
    [handleScroll, pinnedToEnd, scrollToEnd],
  )

  return (
    <MessageScrollerContext.Provider value={value}>
      {children}
    </MessageScrollerContext.Provider>
  )
}

function MessageScroller({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="message-scroller"
      className={cn(
        "relative flex size-full min-h-0 flex-col overflow-hidden",
        className,
      )}
      {...props}
    />
  )
}

function MessageScrollerViewport({
  className,
  ...props
}: React.ComponentProps<"div">) {
  const { viewportRef, handleScroll } = useMessageScroller()
  return (
    <div
      ref={viewportRef}
      data-slot="message-scroller-viewport"
      onScroll={handleScroll}
      className={cn(
        "size-full min-h-0 min-w-0 overflow-y-auto overscroll-contain",
        className,
      )}
      {...props}
    />
  )
}

function MessageScrollerContent({
  className,
  children,
  ...props
}: React.ComponentProps<"div">) {
  const { viewportRef, pinnedToEnd } = useMessageScroller()
  const contentRef = React.useRef<HTMLDivElement | null>(null)

  const stickToEnd = React.useCallback(() => {
    if (!pinnedToEnd) return
    const node = viewportRef.current
    if (!node) return
    node.scrollTop = node.scrollHeight
  }, [viewportRef, pinnedToEnd])

  React.useLayoutEffect(() => {
    stickToEnd()
  })

  React.useEffect(() => {
    const content = contentRef.current
    if (!content || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(() => {
      stickToEnd()
    })
    observer.observe(content)
    return () => observer.disconnect()
  }, [stickToEnd])

  return (
    <div
      ref={contentRef}
      data-slot="message-scroller-content"
      role="log"
      aria-relevant="additions"
      aria-live="polite"
      className={cn("flex h-max min-h-full flex-col gap-6", className)}
      {...props}
    >
      {children}
    </div>
  )
}

function MessageScrollerItem({
  className,
  scrollAnchor = false,
  ...props
}: React.ComponentProps<"div"> & { scrollAnchor?: boolean }) {
  const itemRef = React.useRef<HTMLDivElement | null>(null)

  React.useLayoutEffect(() => {
    if (!scrollAnchor) return
    // Instant only — smooth scroll fights stick-to-bottom during streaming.
    itemRef.current?.scrollIntoView?.({ block: "nearest", behavior: "auto" })
  }, [scrollAnchor])

  return (
    <div
      ref={itemRef}
      data-slot="message-scroller-item"
      data-scroll-anchor={scrollAnchor || undefined}
      className={cn("min-w-0 shrink-0", className)}
      {...props}
    />
  )
}

function MessageScrollerButton({
  className,
  ...props
}: React.ComponentProps<typeof Button>) {
  const { pinnedToEnd, scrollToEnd } = useMessageScroller()

  const handleClick = () => {
    scrollToEnd()
  }

  return (
    <Button
      type="button"
      variant="secondary"
      size="icon-sm"
      data-slot="message-scroller-button"
      data-active={!pinnedToEnd}
      aria-label="Scroll to end"
      tabIndex={pinnedToEnd ? -1 : 0}
      onClick={handleClick}
      className={cn(
        "absolute bottom-4 left-1/2 -translate-x-1/2 border-border bg-background text-foreground transition-[scale,opacity] duration-200",
        pinnedToEnd
          ? "pointer-events-none scale-95 opacity-0"
          : "scale-100 opacity-100",
        className,
      )}
      {...props}
    >
      <ArrowDownIcon />
      <span className="sr-only">Scroll to end</span>
    </Button>
  )
}

export {
  MessageScrollerProvider,
  MessageScroller,
  MessageScrollerViewport,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerButton,
  useMessageScroller,
}
