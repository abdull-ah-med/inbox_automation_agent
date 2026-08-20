"use client"

import { useEffect, useRef, useState } from "react"

import { SmoothStreamText } from "@/lib/smooth-stream-text"

const prefersReducedMotion = (): boolean => {
  if (typeof window === "undefined" || !window.matchMedia) return false
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
}

/**
 * Returns a smoothly revealed prefix of `target` while the network is
 * streaming (or while catching up after the stream ends).
 */
export const useSmoothStreamText = (
  target: string,
  networkStreaming: boolean,
): { visible: string; animating: boolean } => {
  const engineRef = useRef<SmoothStreamText | null>(null)
  if (engineRef.current == null) {
    engineRef.current = new SmoothStreamText({
      enabled: !prefersReducedMotion(),
    })
  }

  const [visible, setVisible] = useState(() => {
    if (prefersReducedMotion() || !networkStreaming) return target
    return ""
  })
  const [animating, setAnimating] = useState(
    () => Boolean(target) && networkStreaming && !prefersReducedMotion(),
  )

  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return

    engine.setTarget(target, networkStreaming)

    if (!engine.isAnimating()) {
      setVisible(engine.getVisible())
      setAnimating(false)
      return
    }

    let cancelled = false
    setAnimating(true)

    const step = (now: number) => {
      if (cancelled) return
      setVisible(engine.tick(now))
      if (engine.isAnimating()) {
        frame = window.requestAnimationFrame(step)
        return
      }
      setAnimating(false)
    }

    let frame = window.requestAnimationFrame(step)
    return () => {
      cancelled = true
      window.cancelAnimationFrame(frame)
    }
  }, [target, networkStreaming])

  return { visible, animating }
}
