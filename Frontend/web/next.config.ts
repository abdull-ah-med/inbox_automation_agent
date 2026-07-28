import type { NextConfig } from "next";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;

if (
  process.env.NODE_ENV === "production" &&
  apiBase &&
  !apiBase.startsWith("https://") &&
  !apiBase.includes("localhost")
) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL must be an https:// URL in production");
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
};

export default nextConfig;
