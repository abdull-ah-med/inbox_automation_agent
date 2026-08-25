"use client"

import { useEffect } from "react"

import { ErrorPage } from "@/components/error-page"

type Options = {
  fullPage?: boolean
  logTag: string
  homeHref?: string
}

export const makeRouteErrorBoundary = ({
  fullPage,
  logTag,
  homeHref = "/dashboard",
}: Options) => {
  const Boundary = ({
    error,
    unstable_retry,
  }: {
    error: Error & { digest?: string }
    unstable_retry: () => void
  }) => {
    useEffect(() => {
      console.error(logTag, error)
    }, [error])
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
