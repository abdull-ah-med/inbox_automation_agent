"use client"

import { Fragment, useState, type ReactNode, type KeyboardEvent } from "react"
import Link from "next/link"

import { EMAIL_QUOTE_PATTERNS } from "@/lib/email-quote-patterns"
import { splitNestedQuotedSegments } from "@/lib/split-nested-quoted-segments"
import { cn, textLinkClass } from "@/lib/utils"
import type { ChatCitation } from "@/lib/types"

// Matches http(s) URLs; stops before common trailing punctuation/brackets so
// "See https://x.com/a)." doesn't swallow the closing paren or period.
const URL_PATTERN = /\bhttps?:\/\/[^\s<>"')\]]+[^\s<>"')\].,;:!?]/g

// InboxAssistant citation markers like [1] matching citation card order (1-based).
const CITATION_MARKER_PATTERN = /\[(\d+)\]/g

const ORIGINAL_MESSAGE_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.originalMessage.source,
  EMAIL_QUOTE_PATTERNS.originalMessage.flags,
)
const UNDERSCORE_SEP_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.underscoreSep.source,
  EMAIL_QUOTE_PATTERNS.underscoreSep.flags,
)
const OUTLOOK_HEADERS_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.outlookHeaders.source,
  EMAIL_QUOTE_PATTERNS.outlookHeaders.flags,
)
const ON_WROTE_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.onWrote.source,
  EMAIL_QUOTE_PATTERNS.onWrote.flags,
)
const ZENDESK_DEFAULT_AVATAR_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.zendeskDefaultAvatar.source,
  EMAIL_QUOTE_PATTERNS.zendeskDefaultAvatar.flags,
)
const ZENDESK_AGENT_PHOTO_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.zendeskAgentPhoto.source,
  `${EMAIL_QUOTE_PATTERNS.zendeskAgentPhoto.flags}g`,
)
const ZENDESK_FOLLOW_UP_PATTERN = new RegExp(
  EMAIL_QUOTE_PATTERNS.zendeskFollowUp.source,
  EMAIL_QUOTE_PATTERNS.zendeskFollowUp.flags,
)

const matchBoundaryAt = (match: RegExpExecArray): number => {
  return match.index + (match[1] ? match[1].length : 0)
}

/** Second system/photos avatar starts prior agent comments in Zendesk dumps. */
const secondZendeskAgentPhotoBoundary = (text: string): number | null => {
  ZENDESK_AGENT_PHOTO_PATTERN.lastIndex = 0
  const matches = [...text.matchAll(ZENDESK_AGENT_PHOTO_PATTERN)]
  if (matches.length < 2) return null
  const second = matches[1]
  if (!second || second.index === undefined) return null
  return second.index + (second[1] ? second[1].length : 0)
}

/**
 * Splits a plain-text email body into the latest reply and any quoted history
 * that Outlook/Gmail typically append (Original Message, From/Sent headers, etc.).
 * An empty unique reply is valid — quote-only bodies must not restore the wall.
 */
export const splitQuotedHistory = (text: string): { main: string; quoted: string | null } => {
  const normalized = text.replaceAll("\r\n", "\n").replaceAll("\r", "\n")
  const candidates: number[] = []

  for (const pattern of [
    ORIGINAL_MESSAGE_PATTERN,
    UNDERSCORE_SEP_PATTERN,
    OUTLOOK_HEADERS_PATTERN,
    ON_WROTE_PATTERN,
    ZENDESK_DEFAULT_AVATAR_PATTERN,
    ZENDESK_FOLLOW_UP_PATTERN,
  ]) {
    pattern.lastIndex = 0
    const match = pattern.exec(normalized)
    if (!match) continue
    candidates.push(matchBoundaryAt(match))
  }

  const agentPhotoAt = secondZendeskAgentPhotoBoundary(normalized)
  if (agentPhotoAt != null) {
    candidates.push(agentPhotoAt)
  }

  if (candidates.length === 0) {
    return { main: normalized, quoted: null }
  }

  const quoteStart = Math.min(...candidates)
  const main = normalized.slice(0, quoteStart).trimEnd()
  const quoted = normalized.slice(quoteStart).trim()

  if (!quoted) {
    return { main: normalized, quoted: null }
  }

  return { main, quoted }
}

// Common LLM draft habit: **Check spam** — render as bold, hide the markers.
const BOLD_PATTERN = /\*\*(.+?)\*\*/g

const linkifyUrls = (text: string, keyPrefix: string): React.ReactNode[] => {
  const nodes: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  let key = 0

  URL_PATTERN.lastIndex = 0
  while ((match = URL_PATTERN.exec(text)) !== null) {
    const [url] = match
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    nodes.push(
      <a
        key={`${keyPrefix}-link-${key++}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="break-words text-blue-600 underline decoration-blue-600/40 decoration-1 underline-offset-2 hover:decoration-blue-600 dark:text-blue-400 dark:decoration-blue-400/40 dark:hover:decoration-blue-400"
      >
        {url}
      </a>,
    )
    lastIndex = match.index + url.length
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }
  return nodes
}

const linkify = (
  text: string,
  keyPrefix: string,
  citations?: ChatCitation[],
): React.ReactNode[] => {
  if (!citations?.length) {
    return linkifyUrls(text, keyPrefix)
  }

  const nodes: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  let key = 0

  CITATION_MARKER_PATTERN.lastIndex = 0
  while ((match = CITATION_MARKER_PATTERN.exec(text)) !== null) {
    const index = Number(match[1])
    const citation = Number.isFinite(index) ? citations[index - 1] : undefined
    if (match.index > lastIndex) {
      nodes.push(...linkifyUrls(text.slice(lastIndex, match.index), `${keyPrefix}-t${key}`))
    }
    if (citation) {
      const subject = citation.subject?.trim() || `thread ${index}`
      const href = citation.url_path || `/threads/${citation.thread_id}`
      nodes.push(
        <Link
          key={`${keyPrefix}-cite-${key++}`}
          href={href}
          aria-label={`Citation ${index}: ${subject}`}
          title={subject}
          className="bg-primary/15 text-primary hover:bg-primary/25 mx-0.5 inline-flex translate-y-[-0.05em] items-center rounded-sm px-1 py-0 text-[0.7rem] font-semibold no-underline"
        >
          [{index}]
        </Link>,
      )
    } else {
      nodes.push(match[0])
    }
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    nodes.push(...linkifyUrls(text.slice(lastIndex), `${keyPrefix}-t${key}`))
  }
  return nodes
}

const renderInline = (text: string, citations?: ChatCitation[]): React.ReactNode[] => {
  const nodes: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  let key = 0

  BOLD_PATTERN.lastIndex = 0
  while ((match = BOLD_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(...linkify(text.slice(lastIndex, match.index), `t${key}`, citations))
    }
    nodes.push(
      <strong key={`bold-${key++}`} className="font-semibold text-gray-800 dark:text-gray-100">
        {linkify(match[1], `b${key}`, citations)}
      </strong>,
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    nodes.push(...linkify(text.slice(lastIndex), `t${key}`, citations))
  }
  return nodes
}

const BodyText = ({ text, citations }: { text: string; citations?: ChatCitation[] }) => {
  return (
    <>
      {renderInline(text, citations).map((node, index) => (
        <Fragment key={index}>{node}</Fragment>
      ))}
    </>
  )
}

const TICKET_UPDATED_RE = /^Your request \(\d+\) has been updated\b.*$/i
const AGENT_BYLINE_RE =
  /^[A-Z][a-zA-Z'\u2019-]{1,40}(?:\s+[A-Z][a-zA-Z'\u2019-]{1,40})?\s*\([^)\n]{2,80}\)\s*$/
const TIP_DATE_RE = /^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b/i

type TipSegment =
  | { kind: "chrome"; text: string }
  | { kind: "byline"; text: string }
  | { kind: "date"; text: string }
  | { kind: "body"; text: string }

/**
 * Light structure for Zendesk-style tips: mute ticket chrome, emphasize agent
 * byline + date. Returns null when the tip does not look like ticket mail.
 */
export const parseTicketTipSegments = (text: string): TipSegment[] | null => {
  const normalized = text.replaceAll("\r\n", "\n").replaceAll("\r", "\n").trim()
  if (!normalized) return null

  const lines = normalized.split("\n")
  const bylineIndex = lines.findIndex((line) => AGENT_BYLINE_RE.test(line.trim()))
  if (bylineIndex < 0) return null

  const segments: TipSegment[] = []
  let cursor = 0

  const pushBody = (from: number, to: number) => {
    const chunk = lines.slice(from, to).join("\n").trim()
    if (chunk) segments.push({ kind: "body", text: chunk })
  }

  // Leading chrome (ticket updated line + blanks) before byline.
  while (cursor < bylineIndex) {
    const line = lines[cursor] ?? ""
    const trimmed = line.trim()
    if (!trimmed) {
      cursor += 1
      continue
    }
    if (TICKET_UPDATED_RE.test(trimmed)) {
      segments.push({ kind: "chrome", text: trimmed })
      cursor += 1
      continue
    }
    break
  }
  if (cursor < bylineIndex) {
    pushBody(cursor, bylineIndex)
  }

  segments.push({ kind: "byline", text: (lines[bylineIndex] ?? "").trim() })
  cursor = bylineIndex + 1
  while (cursor < lines.length && !(lines[cursor] ?? "").trim()) {
    cursor += 1
  }

  const dateLine = (lines[cursor] ?? "").trim()
  if (dateLine && TIP_DATE_RE.test(dateLine)) {
    segments.push({ kind: "date", text: dateLine })
    cursor += 1
    while (cursor < lines.length && !(lines[cursor] ?? "").trim()) {
      cursor += 1
    }
  }

  pushBody(cursor, lines.length)
  return segments.some((s) => s.kind === "byline") ? segments : null
}

const StructuredTipBody = ({
  segments,
  citations,
}: {
  segments: TipSegment[]
  citations?: ChatCitation[]
}) => {
  return (
    <div className="flex flex-col gap-2">
      {segments.map((segment, index) => {
        if (segment.kind === "chrome") {
          return (
            <p
              key={`chrome-${index}`}
              data-email-chrome
              className="text-muted-foreground text-xs leading-relaxed"
            >
              {segment.text}
            </p>
          )
        }
        if (segment.kind === "byline") {
          return (
            <p
              key={`byline-${index}`}
              data-email-byline
              className="text-sm font-medium text-gray-900 dark:text-gray-100"
            >
              {segment.text}
            </p>
          )
        }
        if (segment.kind === "date") {
          return (
            <p key={`date-${index}`} data-email-date className="text-muted-foreground text-xs">
              {segment.text}
            </p>
          )
        }
        return (
          <div key={`body-${index}`} className="break-words wrap-anywhere whitespace-pre-wrap">
            <BodyText text={segment.text} citations={citations} />
          </div>
        )
      })}
    </div>
  )
}

const MainBody = ({ text, citations }: { text: string; citations?: ChatCitation[] }) => {
  const segments = parseTicketTipSegments(text)
  if (segments) {
    return <StructuredTipBody segments={segments} citations={citations} />
  }
  return (
    <div className="break-words wrap-anywhere whitespace-pre-wrap">
      <BodyText text={text} citations={citations} />
    </div>
  )
}

const resolveBodyParts = ({
  text,
  collapseQuotes,
  quotedText,
}: {
  text: string | null | undefined
  collapseQuotes: boolean
  quotedText?: string | null
}): { main: string; quoted: string | null } => {
  if (!collapseQuotes) {
    return { main: text ?? "", quoted: null }
  }

  const split = splitQuotedHistory(text ?? "")
  const provided = quotedText != null ? quotedText.trim() || null : null

  // Prefer split of text for main — never force full text as main when a quote
  // boundary exists (quotedText used to override main and re-show the wall).
  if (split.quoted) {
    return {
      main: split.main,
      quoted: provided ?? split.quoted,
    }
  }

  if (provided) {
    return { main: text ?? "", quoted: provided }
  }

  return split
}

const QuotedLevel = ({
  segments,
  depth,
  citations,
}: {
  segments: string[]
  depth: number
  citations?: ChatCitation[]
}): ReactNode => {
  if (segments.length === 0) return null

  const [head, ...rest] = segments
  if (!head) return null

  return (
    <div
      data-quoted-level={depth}
      className="border-border dark:text-muted-foreground border-l-2 pl-3 break-words wrap-anywhere text-gray-500"
    >
      <MainBody text={head} citations={citations} />
      {rest.length > 0 ? (
        <div className="mt-3">
          <QuotedLevel segments={rest} depth={depth + 1} citations={citations} />
        </div>
      ) : null}
    </div>
  )
}

const QuotedHistoryToggle = ({
  quoted,
  citations,
}: {
  quoted: string
  citations?: ChatCitation[]
}) => {
  const [showQuoted, setShowQuoted] = useState(false)
  const segments = splitNestedQuotedSegments(quoted)

  const handleToggleQuoted = () => {
    setShowQuoted((value) => !value)
  }

  const handleQuotedKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleToggleQuoted()
    }
  }

  return (
    <div className="mt-3">
      <button
        type="button"
        tabIndex={0}
        aria-expanded={showQuoted}
        aria-label={showQuoted ? "Hide quoted earlier messages" : "Show quoted earlier messages"}
        className={cn(textLinkClass, "text-sm")}
        onClick={handleToggleQuoted}
        onKeyDown={handleQuotedKeyDown}
      >
        {showQuoted ? "Hide quoted earlier" : "Quoted earlier"}
      </button>
      {showQuoted ? (
        <div data-quoted-history className="mt-3">
          <QuotedLevel segments={segments} depth={0} citations={citations} />
        </div>
      ) : null}
    </div>
  )
}

/**
 * Renders plain-text email/draft bodies safely: preserves line breaks,
 * wraps arbitrarily long unbroken text (tracking links, IDs) instead of
 * overflowing the container, turns bare URLs into clickable links, and
 * renders lightweight ``**bold**`` markers as emphasis (common in LLM drafts).
 * When collapseQuotes is on, Outlook/Gmail quoted history is hidden until expanded.
 * When citations are provided, ``[n]`` markers link to the matching thread.
 */
export const EmailBody = ({
  text,
  className = "",
  emptyLabel = "(no content)",
  collapseQuotes = false,
  quotedText,
  trailing = null,
  citations,
}: {
  text: string | null | undefined
  className?: string
  emptyLabel?: string
  collapseQuotes?: boolean
  quotedText?: string | null
  trailing?: ReactNode
  citations?: ChatCitation[]
}) => {
  const { main, quoted } = resolveBodyParts({ text, collapseQuotes, quotedText })
  const uniqueEmpty = Boolean(quoted) && !main.trim()

  if (!main.trim() && !quoted) {
    if (trailing) {
      return <div className={cn("text-sm leading-relaxed", className)}>{trailing}</div>
    }
    return <p className={`text-muted-foreground text-sm italic ${className}`}>{emptyLabel}</p>
  }

  return (
    <div
      className={cn(
        "min-w-0 overflow-hidden text-sm leading-relaxed text-gray-700 dark:text-gray-300",
        className,
      )}
    >
      <div>
        {uniqueEmpty ? (
          <p className="text-muted-foreground text-sm italic">No new text in this reply</p>
        ) : (
          <MainBody text={main} citations={citations} />
        )}
        {trailing}
      </div>
      {quoted ? <QuotedHistoryToggle quoted={quoted} citations={citations} /> : null}
    </div>
  )
}
