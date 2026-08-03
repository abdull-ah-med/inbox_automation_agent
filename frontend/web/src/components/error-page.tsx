"use client"

import { useRouter } from "next/navigation"

import { Button } from "@/components/ui/button"
import {
  friendlyErrorFromUnknown,
  type FriendlyError,
} from "@/lib/error-messages"
import { cn } from "@/lib/utils"

type ErrorPageProps = {
  error?: unknown
  title?: string
  description?: string
  digest?: string
  onRetry?: () => void
  homeHref?: string
  className?: string
  /** Full-viewport layout for route-level / global error pages */
  fullPage?: boolean
}

export const ErrorPage = ({
  error,
  title,
  description,
  digest,
  onRetry,
  homeHref = "/dashboard",
  className,
  fullPage = false,
}: ErrorPageProps) => {
  const router = useRouter()

  const mapped: FriendlyError = error
    ? friendlyErrorFromUnknown(error)
    : {
        title: title ?? "Something went wrong",
        description: description ?? "Please try again.",
      }

  const heading = title ?? mapped.title
  const body = description ?? mapped.description
  const reference =
    digest ??
    (typeof error === "object" &&
    error !== null &&
    "digest" in error &&
    typeof (error as { digest?: unknown }).digest === "string"
      ? (error as { digest: string }).digest
      : undefined)

  const handleGoOverview = () => {
    // Replace the error entry so Back skips this page and returns to prior work.
    router.replace(homeHref)
  }

  const handleGoOverviewKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
  ) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleGoOverview()
    }
  }

  const handleRetry = () => {
    onRetry?.()
  }

  const handleRetryKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleRetry()
    }
  }

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={cn(
        fullPage
          ? "flex min-h-screen items-center justify-center bg-gray-50 px-6 py-12 dark:bg-gray-950"
          : "rounded-lg border border-gray-200 bg-white px-6 py-10 dark:border-gray-700 dark:bg-gray-900",
        className,
      )}
    >
      <div className="mx-auto w-full max-w-md text-center">
        <p className="text-xs font-semibold tracking-wide text-blue-600 uppercase">
          Inbox Triage
        </p>
        <h1 className="mt-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
          {heading}
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-gray-500 dark:text-gray-400">
          {body}
        </p>
        {reference ? (
          <p className="mt-3 text-xs text-gray-400">Reference: {reference}</p>
        ) : null}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          {onRetry ? (
            <Button
              type="button"
              tabIndex={0}
              aria-label="Try again"
              className="cursor-pointer bg-blue-600 text-white hover:bg-blue-700"
              onClick={handleRetry}
              onKeyDown={handleRetryKeyDown}
            >
              Try again
            </Button>
          ) : null}
          <Button
            type="button"
            tabIndex={0}
            aria-label="Go to overview"
            variant={onRetry ? "outline" : "default"}
            className={
              onRetry
                ? "cursor-pointer"
                : "cursor-pointer bg-blue-600 text-white hover:bg-blue-700"
            }
            onClick={handleGoOverview}
            onKeyDown={handleGoOverviewKeyDown}
          >
            Go to overview
          </Button>
        </div>
      </div>
    </div>
  )
}
