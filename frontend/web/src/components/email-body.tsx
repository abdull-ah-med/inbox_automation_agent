"use client"

import { Fragment, useState, type ReactNode, type KeyboardEvent } from "react"
import Link from "next/link"

import { cn, textLinkClass } from "@/lib/utils"
import type { ChatCitation } from "@/lib/types"

// Matches http(s) URLs; stops before common trailing punctuation/brackets so
// "See https://x.com/a)." doesn't swallow the closing paren or period.
const URL_PATTERN = /\bhttps?:\/\/[^\s<>"')\]]+[^\s<>"')\].,;:!?]/g

// InboxAssistant citation markers like [1] matching citation card order (1-based).
const CITATION_MARKER_PATTERN = /\[(\d+)\]/g

const ORIGINAL_MESSAGE_PATTERN = /(^|\n)[-\s]*Original Message[-\s]*\s*\n/i
const UNDERSCORE_SEP_PATTERN = /(^|\n)_{10,}\s*\n/
const OUTLOOK_HEADERS_PATTERN =
  /(^|\n)From:\s.+\nSent:\s.+(?:\n(?:To|Cc|Bcc|Subject):.*)*\n/i
const ON_WROTE_PATTERN = /(^|\n)On .+ wrote:\s*\n/i

/**
 * Splits a plain-text email body into the latest reply and any quoted history
 * that Outlook/Gmail typically append (Original Message, From/Sent headers, etc.).
 */
export const splitQuotedHistory = (
  text: string,
): { main: string; quoted: string | null } => {
  const candidates: number[] = []

  for (const pattern of [
    ORIGINAL_MESSAGE_PATTERN,
    UNDERSCORE_SEP_PATTERN,
    OUTLOOK_HEADERS_PATTERN,
    ON_WROTE_PATTERN,
  ]) {
    const match = pattern.exec(text)
    if (!match) continue
    // Prefer the start of the quote marker itself (skip the leading newline).
    const at = match.index + (match[1] ? match[1].length : 0)
    if (at > 0) candidates.push(at)
  }

  if (candidates.length === 0) {
    return { main: text, quoted: null }
  }

  const quoteStart = Math.min(...candidates)
  const main = text.slice(0, quoteStart).trimEnd()
  const quoted = text.slice(quoteStart).trim()

  if (!main || !quoted) {
    return { main: text, quoted: null }
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
      nodes.push(
        ...linkifyUrls(text.slice(lastIndex, match.index), `${keyPrefix}-t${key}`),
      )
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
          className="mx-0.5 inline-flex translate-y-[-0.05em] items-center rounded-sm bg-primary/15 px-1 py-0 text-[0.7rem] font-semibold text-primary no-underline hover:bg-primary/25"
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

const renderInline = (
  text: string,
  citations?: ChatCitation[],
): React.ReactNode[] => {
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
      <strong
        key={`bold-${key++}`}
        className="font-semibold text-gray-800 dark:text-gray-100"
      >
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

const BodyText = ({
  text,
  citations,
}: {
  text: string
  citations?: ChatCitation[]
}) => {
  return (
    <>
      {renderInline(text, citations).map((node, index) => (
        <Fragment key={index}>{node}</Fragment>
      ))}
    </>
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
  trailing = null,
  citations,
}: {
  text: string | null | undefined
  className?: string
  emptyLabel?: string
  collapseQuotes?: boolean
  trailing?: ReactNode
  citations?: ChatCitation[]
}) => {
  const [showQuoted, setShowQuoted] = useState(false)

  if (!text || !text.trim()) {
    if (trailing) {
      return <div className={cn("text-sm leading-relaxed", className)}>{trailing}</div>
    }
    return <p className={`text-sm text-gray-400 italic ${className}`}>{emptyLabel}</p>
  }

  const { main, quoted } = collapseQuotes
    ? splitQuotedHistory(text)
    : { main: text, quoted: null }

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
    <div
      className={cn(
        "min-w-0 overflow-hidden text-sm leading-relaxed text-gray-700 dark:text-gray-300",
        className,
      )}
    >
      <div className="wrap-anywhere whitespace-pre-wrap break-words">
        <BodyText text={main} citations={citations} />
        {trailing}
      </div>
      {quoted ? (
        <div className="mt-3">
          <button
            type="button"
            tabIndex={0}
            aria-expanded={showQuoted}
            aria-label={
              showQuoted ? "Hide quoted earlier messages" : "Show quoted earlier messages"
            }
            className={cn(textLinkClass, "text-sm")}
            onClick={handleToggleQuoted}
            onKeyDown={handleQuotedKeyDown}
          >
            {showQuoted ? "Hide earlier" : "Show earlier"}
          </button>
          {showQuoted ? (
            <div className="mt-3 wrap-anywhere whitespace-pre-wrap break-words border-t border-border pt-3 text-gray-500 dark:text-gray-400">
              <BodyText text={quoted} citations={citations} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
