/**
 * Map HTTP / runtime failures to short, human-readable copy.
 * Prefer these over raw status codes or backend detail strings in the UI.
 */

export type FriendlyError = {
  title: string
  description: string
}

const STATUS_MESSAGES: Record<number, FriendlyError> = {
  400: {
    title: "Invalid request",
    description: "Something in that request was not valid. Please try again.",
  },
  401: {
    title: "Sign in required",
    description: "Your session expired or is missing. Please sign in again.",
  },
  403: {
    title: "Access denied",
    description: "You do not have permission to view this.",
  },
  404: {
    title: "Not found",
    description: "We could not find what you were looking for.",
  },
  408: {
    title: "Request timed out",
    description: "The server took too long to respond. Please try again.",
  },
  413: {
    title: "Request too large",
    description: "That request was too large to process. Please try again with less data.",
  },
  422: {
    title: "Could not process request",
    description: "Some of the information sent was invalid. Please check and try again.",
  },
  429: {
    title: "Too many attempts",
    description: "You have tried too many times. Please try again after some time.",
  },
  500: {
    title: "Something went wrong",
    description: "An unexpected error occurred on our side. Please try again shortly.",
  },
  502: {
    title: "Service unavailable",
    description: "The server is temporarily unreachable. Please try again shortly.",
  },
  503: {
    title: "Service unavailable",
    description: "The service is temporarily unavailable. Please try again shortly.",
  },
  504: {
    title: "Gateway timed out",
    description: "The server took too long to respond. Please try again shortly.",
  },
}

const FALLBACK: FriendlyError = {
  title: "Something went wrong",
  description: "An unexpected error occurred. Please try again.",
}

const NETWORK: FriendlyError = {
  title: "Connection problem",
  description: "Unable to reach the server. Check your connection and try again.",
}

const SERVER_RENDER: FriendlyError = {
  title: "Something went wrong",
  description:
    "This page could not be loaded. Please try again. If it keeps happening, contact your admin.",
}

/** User-facing description for an HTTP status (never just the numeric code). */
export const messageForStatus = (status: number): string => {
  return STATUS_MESSAGES[status]?.description ?? FALLBACK.description
}

export const friendlyErrorForStatus = (status: number): FriendlyError => {
  return STATUS_MESSAGES[status] ?? FALLBACK
}

const looksLikeStatusOnly = (message: string): boolean => {
  const trimmed = message.trim()
  return (
    /^\d{3}$/.test(trimmed) ||
    /^request failed\s*\(\d{3}\)$/i.test(trimmed) ||
    /^error\s*\d{3}$/i.test(trimmed) ||
    /^http\s*\d{3}$/i.test(trimmed)
  )
}

export const friendlyErrorFromUnknown = (error: unknown): FriendlyError => {
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = error.status
    if (typeof status === "number" && STATUS_MESSAGES[status]) {
      return STATUS_MESSAGES[status]
    }
  }

  if (error instanceof TypeError) {
    return NETWORK
  }

  if (error instanceof Error) {
    const digest =
      "digest" in error && typeof (error as { digest?: unknown }).digest === "string"
        ? (error as { digest: string }).digest
        : undefined

    if (digest || /server components/i.test(error.message) || /digest/i.test(error.message)) {
      return SERVER_RENDER
    }

    if (
      error.message === "Failed to fetch" ||
      /networkerror|load failed|failed to fetch/i.test(error.message)
    ) {
      return NETWORK
    }

    if (looksLikeStatusOnly(error.message)) {
      const match = error.message.match(/\d{3}/)
      if (match) {
        return friendlyErrorForStatus(Number(match[0]))
      }
      return FALLBACK
    }

    // Prefer a short known message; avoid dumping stack-like text.
    if (error.message && error.message.length < 160 && !error.message.includes("\n")) {
      return {
        title: FALLBACK.title,
        description: error.message,
      }
    }
  }

  return FALLBACK
}

/** Single-line copy for toasts and compact alerts. */
export const getErrorMessage = (error: unknown): string => {
  return friendlyErrorFromUnknown(error).description
}

export const getMutationErrorMessage = (err: unknown, fallback: string): string =>
  err instanceof Error ? err.message : fallback
