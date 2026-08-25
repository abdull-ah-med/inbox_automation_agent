"use client"

import { makeRouteErrorBoundary } from "@/lib/route-error"
import "./globals.css"

/**
 * Root layout / template failures. Must define its own html and body.
 * Covers server-side render failures that escape the segment boundary.
 */
const Inner = makeRouteErrorBoundary({
  fullPage: true,
  logTag: "app_global_error_boundary",
})

const GlobalError = ({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string }
  unstable_retry: () => void
}) => {
  return (
    <html lang="en">
      <body className="min-h-full bg-gray-50 text-gray-900 antialiased dark:bg-gray-950 dark:text-gray-100">
        <title>Something went wrong — Inbox Triage</title>
        <Inner error={error} unstable_retry={unstable_retry} />
      </body>
    </html>
  )
}

export default GlobalError
