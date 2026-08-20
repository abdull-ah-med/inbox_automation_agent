import { describe, expect, it, vi } from "vitest"

import { createRafDeltaBatcher } from "@/lib/stream-delta-batcher"

describe("createRafDeltaBatcher", () => {
  it("coalesces multiple pushes into one flush per animation frame", () => {
    const frames: FrameRequestCallback[] = []
    const schedule = vi.fn((cb: FrameRequestCallback) => {
      frames.push(cb)
      return frames.length
    })
    const cancel = vi.fn()
    const flushed: string[] = []
    const batcher = createRafDeltaBatcher(
      (chunk) => {
        flushed.push(chunk)
      },
      schedule as unknown as typeof requestAnimationFrame,
      cancel as unknown as typeof cancelAnimationFrame,
    )

    batcher.push("Hel")
    batcher.push("lo")
    batcher.push("!")
    expect(flushed).toEqual([])
    expect(schedule).toHaveBeenCalledTimes(1)

    frames[0](0)
    expect(flushed).toEqual(["Hello!"])
  })

  it("flushNow drains a pending buffer without waiting for the frame", () => {
    const frames: FrameRequestCallback[] = []
    const schedule = vi.fn((cb: FrameRequestCallback) => {
      frames.push(cb)
      return 7
    })
    const cancel = vi.fn()
    const flushed: string[] = []
    const batcher = createRafDeltaBatcher(
      (chunk) => {
        flushed.push(chunk)
      },
      schedule as unknown as typeof requestAnimationFrame,
      cancel as unknown as typeof cancelAnimationFrame,
    )

    batcher.push("partial")
    batcher.flushNow()
    expect(cancel).toHaveBeenCalledWith(7)
    expect(flushed).toEqual(["partial"])

    frames[0]?.(0)
    expect(flushed).toEqual(["partial"])
  })

  it("drain returns buffered text without calling flush", () => {
    const cancel = vi.fn()
    const flushed: string[] = []
    const batcher = createRafDeltaBatcher(
      (chunk) => {
        flushed.push(chunk)
      },
      (() => 3) as unknown as typeof requestAnimationFrame,
      cancel as unknown as typeof cancelAnimationFrame,
    )

    batcher.push("tail")
    expect(batcher.drain()).toBe("tail")
    expect(cancel).toHaveBeenCalledWith(3)
    expect(flushed).toEqual([])
    expect(batcher.drain()).toBe("")
  })

  it("ignores empty pushes", () => {
    const schedule = vi.fn()
    const batcher = createRafDeltaBatcher(
      () => {
        throw new Error("should not flush")
      },
      schedule as unknown as typeof requestAnimationFrame,
    )
    batcher.push("")
    expect(schedule).not.toHaveBeenCalled()
  })
})
