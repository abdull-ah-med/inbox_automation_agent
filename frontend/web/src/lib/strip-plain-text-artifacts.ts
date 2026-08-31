/**
 * Mirror of backend `app.llm.email_clean.strip_plain_text_artifacts`.
 * Keeps Quoted earlier readable the same way reply_text is cleaned.
 */

const REPLY_DELIMITER_RE =
  /^\s*(?:##-\s*)?Please type your reply above this line(?:\s*-##)?\s*$|^\s*--\s*reply above this line\s*--\s*$/i

const normalizeNewlines = (text: string): string =>
  text.replaceAll("\r\n", "\n").replaceAll("\r", "\n")

const collapseBlankLines = (text: string): string => text.replace(/\n{3,}/g, "\n\n").trim()

/** Remove Zendesk delimiters, cid/image placeholders, and nested mailto junk. */
export const stripPlainTextArtifacts = (text: string): string => {
  if (!text) return text

  let out = normalizeNewlines(text)
  out = out
    .split("\n")
    .filter((line) => !REPLY_DELIMITER_RE.test(line))
    .join("\n")
  out = out.replace(/\[cid:[^\]]+\]/gi, "")
  out = out.replace(/\[https?:\/\/[^\]]+\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\]]*)?\]/gi, "")

  for (let i = 0; i < 6; i += 1) {
    const peeled = out
      .replace(/<mailto:[^<>]*(?:<[^>]*>[^<>]*)*>/gi, "")
      .replace(/<mailto:[^>]+>/gi, "")
    if (peeled === out) break
    out = peeled
  }

  out = out.replace(/([^\s<>]+)\s*<(https?:\/\/[^>]+)>/gi, (_match, label: string, url: string) => {
    const trimmedLabel = label.trim()
    const trimmedUrl = url.trim()
    const lower = trimmedLabel.toLowerCase()
    if (lower === "web" || lower === "w" || lower === "link" || trimmedLabel.startsWith("http")) {
      return lower === "web" || lower === "w" ? `${trimmedLabel} ${trimmedUrl}`.trim() : trimmedUrl
    }
    return trimmedLabel
  })

  out = out.replace(/(?<=\S)>+\s*$/gm, "")
  out = out.replace(/^\s*<+(?=\S)/gm, "")
  return collapseBlankLines(out)
}
