"use client"

import Link from "next/link"
import { Moon, Settings, Sun } from "lucide-react"
import { useTheme } from "next-themes"

import { InboxSearch } from "@/components/inbox-search"
import { Button } from "@/components/ui/button"
import { useAuthState, useLogout } from "@/features/auth/use-auth"
import { useIsClient } from "@/hooks/use-is-client"

export function AppHeader() {
  const auth = useAuthState()
  const logout = useLogout()
  const { resolvedTheme, setTheme } = useTheme()
  const isClient = useIsClient()
  const isDark = resolvedTheme === "dark"

  const handleToggleTheme = () => {
    setTheme(isDark ? "light" : "dark")
  }

  return (
    <header className="sticky top-0 z-50 border-b border-gray-200 bg-white px-4 py-3 sm:px-6 dark:border-gray-800 dark:bg-gray-900">
      <div className="app-shell mx-auto grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-3 min-[950px]:grid-cols-[auto_minmax(0,1fr)_auto] min-[950px]:gap-4">
        <div className="min-w-0">
          <p className="text-[11px] font-medium tracking-wide text-gray-500 uppercase dark:text-gray-400">
            SampleSite Support
          </p>
          <Link
            href="/dashboard"
            className="focus-visible:ring-ring block truncate text-lg font-semibold text-gray-900 outline-none focus-visible:ring-2 dark:text-gray-100"
          >
            Inbox Triage Automation
          </Link>
        </div>
        <div className="col-span-2 min-w-0 min-[950px]:col-span-1 min-[950px]:col-start-2 min-[950px]:row-start-1">
          <InboxSearch />
        </div>
        <div className="col-start-2 row-start-1 flex shrink-0 items-center gap-1.5 min-[950px]:col-start-3 sm:gap-2">
          <span className="hidden rounded bg-green-100 px-2 py-1 text-xs font-medium text-green-700 sm:inline dark:bg-green-900/30 dark:text-green-400">
            Read-Only
          </span>
          <span className="hidden text-sm text-gray-500 sm:inline dark:text-gray-400">
            {auth.user?.email}
          </span>
          <Link
            href="/settings"
            tabIndex={0}
            aria-label="Open settings"
            className="border-border hover:bg-muted focus-visible:ring-ring inline-flex size-10 items-center justify-center rounded-lg border text-gray-700 outline-none focus-visible:ring-2 dark:text-gray-200"
          >
            <Settings className="size-4" aria-hidden="true" />
          </Link>
          <Button
            variant="outline"
            size="icon"
            type="button"
            className="size-10"
            aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
            onClick={handleToggleTheme}
          >
            {isClient && isDark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
          </Button>
          <Button
            variant="outline"
            size="sm"
            type="button"
            className="min-h-10 px-2 sm:px-3"
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
