"use client"

import { useEffect } from "react"

import { ErrorPage } from "@/components/error-page"
import "./globals.css"

/**
 * Root layout / template failures. Must define its own html and body.
 * Covers server-side render failures that escape the segment boundary.
 */
export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string }
  unstable_retry: () => void
}) {
  useEffect(() => {
    console.error("app_global_error_boundary", error)
  }, [error])

  return (
    <html lang="en">
      <body className="min-h-full bg-gray-50 text-gray-900 antialiased dark:bg-gray-950 dark:text-gray-100">
        <title>Something went wrong — Inbox Triage</title>
        <ErrorPage
          fullPage
          error={error}
          digest={error.digest}
          onRetry={() => unstable_retry()}
          homeHref="/dashboard"
        />
      </body>
    </html>
  )
}
