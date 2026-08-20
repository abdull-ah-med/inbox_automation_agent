/** Last-inbound courtesy close: no real ask for Elise right now. */

const ASK_RE =
  /\b(please (send|review|confirm|process|reply)|can you|could you|need you to|what is the status)\b/i
const CLOSE_RE =
  /\b(thanks|thank you|sounds good|appreciate it|we are all set|all set)\b/i
const CONDITIONAL_RE = /let me know if you(?:'re| are) unable/i

export const looksLikeCourtesyClose = (body: string): boolean => {
  const text = body.trim()
  if (!text) return false
  if (ASK_RE.test(text)) return false
  return CLOSE_RE.test(text) || CONDITIONAL_RE.test(text)
}
