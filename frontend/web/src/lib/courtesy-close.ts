/** Last-inbound courtesy close: no real ask for Elise right now. */

const ASK_RE =
  /\b(please (send|review|confirm|process|reply)|can you|could you|need you to|what is the status)\b/i
const CLOSE_RE =
  /\b(thanks|thank you|sounds good|appreciate it|we are all set|all set)\b/i
const CONDITIONAL_RE = /let me know if you(?:'re| are) unable/i
const THANKS_LINE_RE = /^\s*(?:thanks|thank you)[!.,]?\s*$/i

/** Same cut as backend ``strip_quoted_reply`` — newest reply only. */
const GMAIL_ON_WROTE_RE = /^\s*On .+ wrote:\s*$/im
const OUTLOOK_ORIGINAL_RE = /^-{2,}\s*Original Message\s*-{2,}\s*$/im
const OUTLOOK_SEP_RE = /^\s*_{5,}\s*$/im
const QUOTE_LINE_RE = /^>.*(?:\n|$)/gm
const MIN_REMAINDER_CHARS = 20

const latestInboundOnly = (body: string): string => {
  const original = body.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
  let working = original

  const gmail = GMAIL_ON_WROTE_RE.exec(working)
  const outlook = OUTLOOK_ORIGINAL_RE.exec(working)
  const outlookSep = OUTLOOK_SEP_RE.exec(working)
  const cutIndexes = [gmail, outlook, outlookSep]
    .filter((match): match is RegExpExecArray => match != null)
    .map((match) => match.index)
  if (cutIndexes.length > 0) {
    working = working.slice(0, Math.min(...cutIndexes)).trimEnd()
  }

  working = working.replace(QUOTE_LINE_RE, "")

  const remainder = working.trim()
  if (remainder.length < MIN_REMAINDER_CHARS) {
    return original.trim()
  }
  return remainder
}

export const looksLikeCourtesyClose = (body: string): boolean => {
  const text = latestInboundOnly(body)
  if (!text) return false
  const remainder = text
    .split("\n")
    .filter((line) => !THANKS_LINE_RE.test(line))
    .join("\n")
    .trim()
  if (!remainder) return true
  if (ASK_RE.test(remainder)) return false
  return CLOSE_RE.test(remainder) || CONDITIONAL_RE.test(remainder)
}
