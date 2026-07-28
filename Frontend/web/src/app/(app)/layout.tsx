"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { AppHeader } from "@/components/app-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthBootstrap, useAuthState } from "@/features/auth/use-auth";

export default function AppShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { bootstrapped } = useAuthBootstrap();
  const auth = useAuthState();
  const router = useRouter();

  useEffect(() => {
    if (bootstrapped && !auth.accessToken) {
      const next = window.location.pathname + window.location.search;
      const login =
        next && next !== "/login"
          ? `/login?next=${encodeURIComponent(next)}`
          : "/login";
      router.replace(login);
    }
  }, [bootstrapped, auth.accessToken, router]);

  if (!bootstrapped) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
        <div className="mx-auto max-w-7xl space-y-4 p-6">
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-40 w-full rounded-lg" />
        </div>
      </div>
    );
  }

  if (!auth.accessToken) {
    return null;
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-blue-600 focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-white"
      >
        Skip to main content
      </a>
      <AppHeader />
      <main id="main-content" className="mx-auto max-w-7xl p-6">
        {children}
      </main>
    </div>
  );
}
