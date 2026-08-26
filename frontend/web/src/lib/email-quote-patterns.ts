/** Generated from backend/app/core/email_quotes.py — do not hand-edit. */

export const EMAIL_QUOTE_PATTERNS = {
  originalMessage: { source: String.raw`(^|\n)[-\s]*Original Message[-\s]*\s*\n`, flags: "i" },
  underscoreSep: { source: String.raw`(^|\n)_{10,}\s*\n`, flags: "" },
  outlookHeaders: {
    source: String.raw`(^|\n)From:\s.+\n(?:\s*\n)?(?:Sent|Date):\s.+(?:\n(?:To|Cc|Bcc|Subject):.*)*\n`,
    flags: "i",
  },
  onWrote: { source: String.raw`(^|\n)On .+ wrote:\s*\n`, flags: "i" },
} as const
