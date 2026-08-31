/** Adaptive rates for ChatGPT-style character reveal (chars/sec). */
const BASE_CHARS_PER_SECOND = 200
const MAX_CHARS_PER_SECOND = 480
const DEFAULT_MAX_LAG_CHARS = 80

export type SmoothStreamTextOptions = {
  enabled?: boolean
  baseCharsPerSecond?: number
  maxCharsPerSecond?: number
  maxLagChars?: number
}

export const charsPerSecondForLag = (
  lagChars: number,
  base = BASE_CHARS_PER_SECOND,
  max = MAX_CHARS_PER_SECOND,
  maxLag = DEFAULT_MAX_LAG_CHARS,
): number => {
  if (lagChars <= 0) return base
  const t = Math.min(1, lagChars / maxLag)
  return Math.round(base + (max - base) * t)
}

const segmentGraphemes = (text: string): string[] => {
  if (typeof Intl !== "undefined" && "Segmenter" in Intl) {
    const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" })
    return Array.from(segmenter.segment(text), (part) => part.segment)
  }
  return Array.from(text)
}

/**
 * Decouples network chunk arrival from on-screen reveal so answers paint
 * at a steady cadence (ChatGPT / Gemini style) instead of in bursts.
 */
export class SmoothStreamText {
  private enabled: boolean
  private baseCharsPerSecond: number
  private maxCharsPerSecond: number
  private maxLagChars: number
  private target = ""
  private visibleCount = 0
  private graphemes: string[] = []
  private budget = 0
  private lastTs: number | null = null
  private networkStreaming = false

  constructor(options: SmoothStreamTextOptions = {}) {
    this.enabled = options.enabled ?? true
    this.baseCharsPerSecond = options.baseCharsPerSecond ?? BASE_CHARS_PER_SECOND
    this.maxCharsPerSecond = options.maxCharsPerSecond ?? MAX_CHARS_PER_SECOND
    this.maxLagChars = options.maxLagChars ?? DEFAULT_MAX_LAG_CHARS
  }

  setTarget(text: string, networkStreaming: boolean): void {
    if (text.length < this.target.length || !text.startsWith(this.target)) {
      // Target shrank or was replaced — reset visible prefix.
      this.visibleCount = 0
      this.budget = 0
      this.lastTs = null
    }
    this.target = text
    this.graphemes = segmentGraphemes(text)
    this.networkStreaming = networkStreaming
    if (!this.enabled) {
      this.visibleCount = this.graphemes.length
      this.budget = 0
    } else if (this.visibleCount > this.graphemes.length) {
      this.visibleCount = this.graphemes.length
    }
  }

  getVisible(): string {
    return this.graphemes.slice(0, this.visibleCount).join("")
  }

  isCaughtUp(): boolean {
    return this.visibleCount >= this.graphemes.length
  }

  isAnimating(): boolean {
    return this.networkStreaming || !this.isCaughtUp()
  }

  tick(nowMs: number): string {
    if (!this.enabled) {
      this.visibleCount = this.graphemes.length
      this.lastTs = nowMs
      return this.getVisible()
    }

    const lag = this.graphemes.length - this.visibleCount
    if (lag <= 0) {
      this.lastTs = nowMs
      this.budget = 0
      return this.getVisible()
    }

    if (lag > this.maxLagChars) {
      this.visibleCount = this.graphemes.length - this.maxLagChars
      this.budget = 0
    }

    if (this.lastTs == null) {
      this.lastTs = nowMs
      // First paint: show at least one grapheme so the bubble appears immediately.
      if (this.visibleCount === 0 && this.graphemes.length > 0) {
        this.visibleCount = 1
      }
      return this.getVisible()
    }

    const elapsedSec = Math.max(0, (nowMs - this.lastTs) / 1000)
    this.lastTs = nowMs
    const rate = charsPerSecondForLag(
      this.graphemes.length - this.visibleCount,
      this.baseCharsPerSecond,
      this.maxCharsPerSecond,
      this.maxLagChars,
    )
    this.budget += elapsedSec * rate
    const reveal = Math.floor(this.budget)
    if (reveal > 0) {
      this.budget -= reveal
      this.visibleCount = Math.min(this.graphemes.length, this.visibleCount + reveal)
    }
    return this.getVisible()
  }
}
