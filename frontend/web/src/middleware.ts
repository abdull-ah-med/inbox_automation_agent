import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"

/**
 * Auth is not gated here.
 *
 * Do NOT gate routes on the refresh cookie: `itr_refresh` is HttpOnly,
 * Path=/auth, and set by the API origin. The browser never sends it on
 * Next.js document requests to /dashboard, so a cookie check always 307s
 * back to /login after a successful sign-in.
 *
 * Auth is enforced client-side (in-memory access token + silent refresh).
 *
 * Static security headers (CSP, HSTS, X-Frame-Options, …) live in
 * `next.config.ts` `headers()` so they apply to every route. This middleware
 * stays as a passthrough for future request-scoped work (e.g. CSP nonces).
 *
 * CSP notes (Next.js):
 * https://nextjs.org/docs/app/building-your-application/configuring/content-security-policy
 * Full nonce + strict-dynamic is the long-term target.
 */
export function middleware(_request: NextRequest) {
  return NextResponse.next()
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
