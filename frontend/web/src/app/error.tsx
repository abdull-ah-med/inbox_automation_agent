"use client"

import { useEffect } from "react"

import { ErrorPage } from "@/components/error-page"

/**
 * Segment error boundary (client-side render failures in the app tree).
 * Does not wrap the root layout — see global-error.tsx for that.
 */
export default function Error({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string }
  unstable_retry: () => void
}) {
  useEffect(() => {
    console.error("app_error_boundary", error)
  }, [error])

  return (
    <ErrorPage
      fullPage
      error={error}
      digest={error.digest}
      onRetry={() => unstable_retry()}
      homeHref="/dashboard"
    />
  )
}
