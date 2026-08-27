"use client"

import { makeRouteErrorBoundary } from "@/lib/route-error"

/**
 * Segment error boundary (client-side render failures in the app tree).
 * Does not wrap the root layout — see global-error.tsx for that.
 */
export default makeRouteErrorBoundary({
  fullPage: true,
})
