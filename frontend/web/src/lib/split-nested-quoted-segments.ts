import { EMAIL_QUOTE_PATTERNS } from "@/lib/email-quote-patterns"
import { stripPlainTextArtifacts } from "@/lib/strip-plain-text-artifacts"

const ORIGINAL_MESSAGE_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.originalMessage.source,
  `${EMAIL_QUOTE_PATTERNS.originalMessage.flags}g`,
)
const UNDERSCORE_SEP_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.underscoreSep.source,
  `${EMAIL_QUOTE_PATTERNS.underscoreSep.flags}g`,
)
const OUTLOOK_HEADERS_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.outlookHeaders.source,
  `${EMAIL_QUOTE_PATTERNS.outlookHeaders.flags}g`,
)
const ON_WROTE_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.onWrote.source,
  `${EMAIL_QUOTE_PATTERNS.onWrote.flags}g`,
)
const ZENDESK_DEFAULT_AVATAR_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.zendeskDefaultAvatar.source,
  `${EMAIL_QUOTE_PATTERNS.zendeskDefaultAvatar.flags}g`,
)
const ZENDESK_AGENT_PHOTO_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.zendeskAgentPhoto.source,
  `${EMAIL_QUOTE_PATTERNS.zendeskAgentPhoto.flags}g`,
)
const ZENDESK_FOLLOW_UP_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.zendeskFollowUp.source,
  `${EMAIL_QUOTE_PATTERNS.zendeskFollowUp.flags}g`,
)

const SEPARATOR_ONLY_RE = /^[\s_]*$/

const matchBoundaryAt = (match: RegExpExecArray): number => {
  return match.index + (match[1] ? match[1].length : 0)
}

const collectSegmentStarts = (text: string): number[] => {
  const starts = new Set<number>([0])

  for (const pattern of [
    ORIGINAL_MESSAGE_PATTERN,
    UNDERSCORE_SEP_PATTERN,
    OUTLOOK_HEADERS_PATTERN,
    ON_WROTE_PATTERN,
    ZENDESK_DEFAULT_AVATAR_PATTERN,
    ZENDESK_AGENT_PHOTO_PATTERN,
    ZENDESK_FOLLOW_UP_PATTERN,
  ]) {
    pattern.lastIndex = 0
    for (const match of text.matchAll(pattern)) {
      if (match.index === undefined) continue
      const at = matchBoundaryAt(match)
      if (at > 0) starts.add(at)
    }
  }

  return [...starts].toSorted((a, b) => a - b)
}

/**
 * Split a quoted-history wall into nested layers (newest quoted first).
 * Each Outlook From/Sent block, underscore sep, or Zendesk avatar starts a layer.
 * Boundaries are found before artifact stripping so avatar URL markers still work.
 */
export const splitNestedQuotedSegments = (text: string): string[] => {
  const normalized = text.replaceAll("\r\n", "\n").replaceAll("\r", "\n").trim()
  if (!normalized) return []

  const starts = collectSegmentStarts(normalized)
  const segments: string[] = []

  for (let i = 0; i < starts.length; i += 1) {
    const start = starts[i] ?? 0
    const end = starts[i + 1] ?? normalized.length
    const chunk = stripPlainTextArtifacts(normalized.slice(start, end).trim())
    if (!chunk || SEPARATOR_ONLY_RE.test(chunk)) continue
    segments.push(chunk)
  }

  return segments.length > 0 ? segments : [stripPlainTextArtifacts(normalized)]
}
