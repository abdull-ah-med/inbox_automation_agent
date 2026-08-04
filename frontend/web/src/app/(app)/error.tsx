"use client"

import { useEffect } from "react"

import { ErrorPage } from "@/components/error-page"

/** Errors inside the authenticated app shell (keeps the header visible). */
export default function AppError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string }
  unstable_retry: () => void
}) {
  useEffect(() => {
    console.error("app_shell_error_boundary", error)
  }, [error])

  return (
    <ErrorPage
      error={error}
      digest={error.digest}
      onRetry={() => unstable_retry()}
      homeHref="/dashboard"
    />
  )
}
