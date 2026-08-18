import type { NextConfig } from "next";

/**
 * Security headers for every response.
 *
 * Prefer next.config headers() for static policies that apply to all routes
 * (including /, errors, and static assets). Middleware alone with a narrow
 * matcher leaves gaps.
 *
 * Refs:
 * - https://nextjs.org/docs/app/api-reference/config/next-config-js/headers
 * - https://nextjs.org/docs/app/guides/content-security-policy
 * - https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites
 */

const rawApiBase = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";

if (
  process.env.NODE_ENV === "production" &&
  process.env.NEXT_PUBLIC_API_BASE_URL &&
  !process.env.NEXT_PUBLIC_API_BASE_URL.startsWith("https://") &&
  !process.env.NEXT_PUBLIC_API_BASE_URL.includes("localhost")
) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL must be an https:// URL in production");
}

const isProd = process.env.NODE_ENV === "production";

const scriptSrc = isProd
  ? "script-src 'self' 'unsafe-inline'"
  : "script-src 'self' 'unsafe-inline' 'unsafe-eval'";

// Same-origin (empty API base) uses connect-src 'self' only — matches local
// Next rewrites and production nginx. Cross-origin API base is listed explicitly.
const connectSrc = rawApiBase
  ? `connect-src 'self' ${rawApiBase}`
  : "connect-src 'self'";

const contentSecurityPolicy = [
  "default-src 'self'",
  connectSrc,
  "img-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  scriptSrc,
  "font-src 'self' data:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const securityHeaders: { key: string; value: string }[] = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "geolocation=(), camera=(), microphone=()",
  },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
];

if (isProd) {
  // HSTS: only when production HTTPS is guaranteed.
  // https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Strict-Transport-Security
  securityHeaders.push({
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  });
}

const nextConfig: NextConfig = {
  // Standalone output for minimal Docker images (Next.js output file tracing).
  // https://nextjs.org/docs/app/api-reference/config/next-config-js/output
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
  /**
   * Local: proxy /auth and /api to FastAPI so cookies (including CSRF) are
   * same-origin with the SPA — same pattern as production nginx.
   * Production builds skip rewrites (nginx terminates TLS and proxies).
   *
   * https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites
   */
  async rewrites() {
    if (isProd) {
      return [];
    }
    const backend =
      process.env.BACKEND_PROXY_URL?.replace(/\/$/, "") ?? "http://localhost:8000";
    return [
      { source: "/auth/:path*", destination: `${backend}/auth/:path*` },
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
    ];
  },
};

export default nextConfig;
