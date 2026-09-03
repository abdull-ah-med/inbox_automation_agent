/** Parse backend manual-resolve audit bodies into banner lines (see resolution_service). */

const HEADER_PREFIX_RE =
  /^(?:You marked this thread resolved\.|Recorded how you resolved this thread\.)\s*/i

const REMOVED_LINE = "Removed from Needs Attention."

const URGENCY_RE = /^Assessed urgency was ([^;]+); it no longer drives priority\.\s*/i

const ACTIONS_RE = /^Actions taken:\s*([\s\S]+?)(?:\s+With:\s*([\s\S]+))?$/i

const REOPEN_LINE = "You can reopen if work is still open."

export const formatManualResolveLines = (
  auditBody: string | null | undefined,
  urgencyAssessed: string | null,
): string[] => {
  if (!auditBody?.trim()) {
    return [
      REMOVED_LINE,
      ...(urgencyAssessed
        ? [`Assessed urgency was ${urgencyAssessed}; it no longer drives priority.`]
        : []),
      REOPEN_LINE,
    ]
  }

  let rest = auditBody.trim().replace(HEADER_PREFIX_RE, "")

  const lines: string[] = []

  if (rest.startsWith(REMOVED_LINE)) {
    lines.push(REMOVED_LINE)
    rest = rest.slice(REMOVED_LINE.length).trim()
  }

  const urgencyMatch = rest.match(URGENCY_RE)
  if (urgencyMatch) {
    lines.push(`Assessed urgency was ${urgencyMatch[1]}; it no longer drives priority.`)
    rest = rest.slice(urgencyMatch[0].length).trim()
  }

  const actionsMatch = rest.match(ACTIONS_RE)
  if (actionsMatch) {
    lines.push(`Actions taken: ${actionsMatch[1].trim()}`)
    if (actionsMatch[2]?.trim()) {
      lines.push(`With: ${actionsMatch[2].trim()}`)
    }
  } else if (rest) {
    lines.push(rest)
  }

  lines.push(REOPEN_LINE)
  return lines
}
