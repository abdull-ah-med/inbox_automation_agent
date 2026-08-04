"use client"

import { useEffect } from "react"

import { AppHeader } from "@/components/app-header"
import { Skeleton } from "@/components/ui/skeleton"
import { clearAuthSession } from "@/features/auth/auth-store"
import { useAuthBootstrap, useAuthState } from "@/features/auth/use-auth"
import {
  hasLogoutGuard,
  replaceToLogin,
} from "@/lib/auth-navigation"

export default function AppShellLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { bootstrapped } = useAuthBootstrap()
  const auth = useAuthState()

  useEffect(() => {
    const sendToLogin = () => {
      const next = window.location.pathname + window.location.search
      replaceToLogin(next)
    }

    const enforceSignedOut = () => {
      // After logout (or session drop), block bfcache restore of this shell.
      if (hasLogoutGuard()) {
        clearAuthSession()
        sendToLogin()
        return true
      }
      if (bootstrapped && !auth.accessToken) {
        sendToLogin()
        return true
      }
      return false
    }

    enforceSignedOut()

    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        enforceSignedOut()
      }
    }

    window.addEventListener("pageshow", handlePageShow)
    return () => window.removeEventListener("pageshow", handlePageShow)
  }, [bootstrapped, auth.accessToken])

  if (!bootstrapped) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
        <div className="mx-auto max-w-8xl space-y-4 p-15">
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-40 w-full rounded-lg" />
        </div>
      </div>
    )
  }

  if (!auth.accessToken) {
    return null
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
      <main id="main-content" className="mx-auto max-w-8xl p-15">
        {children}
      </main>
    </div>
  )
}
