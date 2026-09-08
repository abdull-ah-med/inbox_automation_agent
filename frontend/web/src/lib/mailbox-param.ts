export const decodeMailboxParam = (raw: string): string | null => {
  try {
    return decodeURIComponent(raw)
  } catch {
    return null
  }
}
