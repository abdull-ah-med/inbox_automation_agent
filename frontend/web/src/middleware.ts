import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"

/**
 * Security headers only.
 *
 * Do NOT gate routes on the refresh cookie here: `itr_refresh` is HttpOnly,
 * Path=/auth, and set by the API origin (localhost:8000). The browser never
 * sends it on Next.js document requests to /dashboard, so a cookie check
 * always 307s back to /login after a successful sign-in.
 *
 * Auth is enforced client-side (in-memory access token + silent refresh).
 *
 * CSP notes (Next.js):
 * https://nextjs.org/docs/app/building-your-application/configuring/content-security-policy
 * Full nonce + strict-dynamic is the long-term target. Until then:
 * - style-src needs 'unsafe-inline' for Tailwind/runtime styles
 * - script-src keeps 'unsafe-inline' for Next hydration without nonce plumbing
 * - 'unsafe-eval' is only needed in development (Turbopack/webpack); omit in prod
 */
export function middleware(_request: NextRequest) {
  const response = NextResponse.next()
  response.headers.set("X-Frame-Options", "DENY")
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin")
  response.headers.set(
    "Permissions-Policy",
    "geolocation=(), camera=(), microphone=()",
  )
  response.headers.set("X-Content-Type-Options", "nosniff")
  const apiBase =
    process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ??
    "http://localhost:8000"
  const isProd = process.env.NODE_ENV === "production"
  const scriptSrc = isProd
    ? "script-src 'self' 'unsafe-inline'"
    : "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
  response.headers.set(
    "Content-Security-Policy",
    [
      "default-src 'self'",
      `connect-src 'self' ${apiBase}`,
      "img-src 'self' data:",
      "style-src 'self' 'unsafe-inline'",
      scriptSrc,
      "font-src 'self' data:",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; "),
  )
  return response
}

export const config = {
  matcher: [
    "/login",
    "/dashboard/:path*",
    "/mailboxes/:path*",
    "/threads/:path*",
    "/settings/:path*",
  ],
}
