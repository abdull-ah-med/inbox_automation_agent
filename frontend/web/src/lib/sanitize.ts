export const sanitizeUserText = (value: string): string => {
  const withoutTags = value.replace(/<[^>]*>/g, " ")
  const withoutControls = withoutTags.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
  const withoutFts = withoutControls.replace(/[&|!():*'"]/g, " ")
  return withoutFts.replace(/\s+/g, " ").trim()
}
