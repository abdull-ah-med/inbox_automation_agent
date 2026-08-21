import { describe, expect, it } from "vitest"

import {
  SmoothStreamText,
  charsPerSecondForLag,
} from "@/lib/smooth-stream-text"

describe("charsPerSecondForLag", () => {
  it("speeds up as the visible text falls behind the target", () => {
    expect(charsPerSecondForLag(0)).toBe(200)
    expect(charsPerSecondForLag(40)).toBeGreaterThan(200)
    expect(charsPerSecondForLag(120)).toBe(480)
    expect(charsPerSecondForLag(999)).toBe(480)
  })
})

describe("SmoothStreamText", () => {
  it("reveals target text gradually instead of dumping the whole chunk", () => {
    const stream = new SmoothStreamText()
    stream.setTarget("The overdue billing dispute", true)

    const afterOneTick = stream.tick(0)
    expect(afterOneTick.length).toBeGreaterThan(0)
    expect(afterOneTick.length).toBeLessThan("The overdue billing dispute".length)
    expect("The overdue billing dispute".startsWith(afterOneTick)).toBe(true)

    const later = stream.tick(100)
    expect(later.length).toBeGreaterThan(afterOneTick.length)
    expect("The overdue billing dispute".startsWith(later)).toBe(true)
  })

  it("never splits a multi-code-point character while revealing", () => {
    const stream = new SmoothStreamText()
    stream.setTarget("Hi 👩‍💻 done", true)

    let visible = ""
    for (let t = 0; t < 2_000; t += 16) {
      visible = stream.tick(t)
      // Surrogate pairs / ZWJ sequences must remain well-formed prefixes of the target.
      expect("Hi 👩‍💻 done".startsWith(visible)).toBe(true)
      if (visible === "Hi 👩‍💻 done") break
    }
    expect(visible).toBe("Hi 👩‍💻 done")
  })

  it("jumps forward when lag exceeds the max visual lag", () => {
    const stream = new SmoothStreamText({ maxLagChars: 20 })
    stream.setTarget("a".repeat(100), true)
    const visible = stream.tick(0)
    expect(visible.length).toBeGreaterThanOrEqual(80)
  })

  it("keeps draining after the network stream ends until caught up", () => {
    const stream = new SmoothStreamText()
    stream.setTarget("Short answer here", true)
    stream.tick(0)
    stream.setTarget("Short answer here", false)
    expect(stream.isCaughtUp()).toBe(false)

    let visible = ""
    for (let t = 16; t < 2_000; t += 16) {
      visible = stream.tick(t)
      if (stream.isCaughtUp()) break
    }
    expect(visible).toBe("Short answer here")
    expect(stream.isCaughtUp()).toBe(true)
  })

  it("shows the full target immediately when smoothing is disabled", () => {
    const stream = new SmoothStreamText({ enabled: false })
    stream.setTarget("Instant dump", true)
    expect(stream.tick(0)).toBe("Instant dump")
    expect(stream.isCaughtUp()).toBe(true)
  })

  it("does not reset visible prefix when the target only grows on completion", () => {
    const stream = new SmoothStreamText()
    stream.setTarget("The overdue", true)
    stream.tick(0)
    const mid = stream.tick(50)
    expect(mid.length).toBeGreaterThan(0)

    // Normal completion: same prefix, longer string, networkStreaming=false.
    stream.setTarget("The overdue billing dispute", false)
    const afterComplete = stream.getVisible()
    expect(afterComplete).toBe(mid)
    expect("The overdue billing dispute".startsWith(afterComplete)).toBe(true)
    expect(stream.isCaughtUp()).toBe(false)
  })

  it("keeps revealing continuously through a bursty target update", () => {
    const stream = new SmoothStreamText({
      baseCharsPerSecond: 240,
      maxCharsPerSecond: 480,
      maxLagChars: 80,
    })
    const full =
      "The overdue billing dispute is waiting on review and Ashley still needs the certificate."
    stream.setTarget(full.slice(0, 20), true)
    stream.tick(0)

    // Burst arrives all at once (cache-like / proxy-buffered SSE).
    stream.setTarget(full, true)

    const samples: number[] = []
    let last = 0
    for (let t = 16; t <= 800; t += 16) {
      const visible = stream.tick(t)
      samples.push(visible.length)
      expect(visible.length).toBeGreaterThanOrEqual(last)
      expect(full.startsWith(visible)).toBe(true)
      last = visible.length
      if (stream.isCaughtUp()) break
    }
    expect(samples.some((n) => n > 0)).toBe(true)
    expect(samples[samples.length - 1]).toBe(full.length)
    // No multi-frame stalls once reveal has started (gaps > 4 frames / 64ms of zero growth).
    let zeroGrowth = 0
    let maxZero = 0
    for (let i = 1; i < samples.length; i += 1) {
      if (samples[i] === samples[i - 1] && samples[i] < full.length) {
        zeroGrowth += 1
        maxZero = Math.max(maxZero, zeroGrowth)
      } else {
        zeroGrowth = 0
      }
    }
    expect(maxZero).toBeLessThanOrEqual(3)
  })
})
