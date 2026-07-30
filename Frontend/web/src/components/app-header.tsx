"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Moon, Settings, Sun } from "lucide-react"
import { useTheme } from "next-themes"

import { Button } from "@/components/ui/button"
import { useAuthState, useLogout } from "@/features/auth/use-auth"

export function AppHeader() {
  const auth = useAuthState()
  const logout = useLogout()
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  const isDark = resolvedTheme === "dark"

  const handleToggleTheme = () => {
    setTheme(isDark ? "light" : "dark")
  }

  return (
    <header className="border-b border-gray-200 bg-white px-6 py-4 dark:border-gray-800 dark:bg-gray-900">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/dashboard"
            className="text-lg font-semibold text-gray-900 dark:text-gray-100"
          >
            Inbox Triage Automation
          </Link>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Read-only assistant for inbox classification, drafting, and review
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="rounded bg-green-100 px-2 py-1 text-xs font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
            Read-Only
          </span>
          <span className="hidden text-sm text-gray-500 sm:inline dark:text-gray-400">
            {auth.user?.email}
          </span>
          <Link
            href="/settings"
            tabIndex={0}
            aria-label="Open settings"
            className="inline-flex size-7 items-center justify-center rounded-lg border border-border text-gray-700 hover:bg-muted dark:text-gray-200"
          >
            <Settings className="size-4" aria-hidden="true" />
          </Link>
          <Button
            variant="outline"
            size="icon-sm"
            type="button"
            aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
            onClick={handleToggleTheme}
          >
            {mounted && isDark ? (
              <Sun aria-hidden="true" />
            ) : (
              <Moon aria-hidden="true" />
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            type="button"
            disabled={logout.isPending}
            onClick={() => logout.mutate()}
          >
            {logout.isPending ? "Signing out…" : "Sign out"}
          </Button>
        </div>
      </div>
    </header>
  )
}
