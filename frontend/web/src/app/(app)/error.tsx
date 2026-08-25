"use client"

import { makeRouteErrorBoundary } from "@/lib/route-error"

/** Errors inside the authenticated app shell (keeps the header visible). */
export default makeRouteErrorBoundary({
  logTag: "app_shell_error_boundary",
})
