import { looksLikeCourtesyClose } from "@/lib/courtesy-close"

export const CourtesyCloseBanner = ({
  state,
  lastInboundBody,
}: {
  state: string
  lastInboundBody: string | null
}) => {
  if (state !== "DRAFTED") return null
  if (!lastInboundBody) return null
  if (!looksLikeCourtesyClose(lastInboundBody)) return null
  return (
    <p
      role="status"
      className="mb-5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100"
    >
      Courtesy close. No reply needed. Click Reject, then choose Wrong action / no reply needed to
      mark this done and remove it from Needs Attention.
    </p>
  )
}
