"use client"

import { ErrorPage } from "@/components/error-page"

type Options = {
  fullPage?: boolean
  homeHref?: string
}

export const makeRouteErrorBoundary = ({ fullPage, homeHref = "/dashboard" }: Options) => {
  const Boundary = ({
    error,
    unstable_retry,
  }: {
    error: Error & { digest?: string }
    unstable_retry: () => void
  }) => {
    return (
      <ErrorPage
        fullPage={fullPage}
        error={error}
        digest={error.digest}
        onRetry={() => unstable_retry()}
        homeHref={homeHref}
      />
    )
  }
  return Boundary
}
