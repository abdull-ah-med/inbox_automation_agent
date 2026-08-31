"use client"

import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query"
import { usePathname } from "next/navigation"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react"

import { getAuthState, subscribeAuth } from "@/features/auth/auth-store"
import { api } from "@/lib/api-client"
import { replaceToLogin } from "@/lib/auth-navigation"

const AuthBootstrapContext = createContext<{ bootstrapped: boolean }>({
  bootstrapped: false,
})

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  })
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [queryClient] = useState(makeQueryClient)
  const [bootstrapped, setBootstrapped] = useState(false)
  const auth = useSyncExternalStore(subscribeAuth, getAuthState, getAuthState)
  const pathname = usePathname()
  const bootstrappedOnce = useRef(false)

  const silentRefresh = useCallback(async () => {
    // Already have an access token (e.g. just logged in) — skip refresh.
    if (getAuthState().accessToken) {
      setBootstrapped(true)
      return
    }
    // Login page: do not auto-refresh (avoids 429 spam + redirect loops).
    if (pathname === "/login" || pathname?.startsWith("/login")) {
      setBootstrapped(true)
      return
    }
    try {
      await api.refresh()
    } finally {
      setBootstrapped(true)
    }
  }, [pathname])

  useEffect(() => {
    if (bootstrappedOnce.current) return
    bootstrappedOnce.current = true
    void silentRefresh()
  }, [silentRefresh])

  // Proactive refresh 30s before expiry.
  useEffect(() => {
    if (!auth.expiresAt) return
    const delay = Math.max(auth.expiresAt - Date.now() - 30_000, 5_000)
    const timer = window.setTimeout(() => {
      void api.refresh()
    }, delay)
    return () => window.clearTimeout(timer)
  }, [auth.expiresAt])

  useEffect(() => {
    const onFocus = () => {
      const { expiresAt, accessToken } = getAuthState()
      if (accessToken && expiresAt && expiresAt < Date.now()) {
        void api.refresh()
      }
    }
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [])

  return (
    <QueryClientProvider client={queryClient}>
      <AuthBootstrapContext.Provider value={{ bootstrapped }}>
        {children}
      </AuthBootstrapContext.Provider>
    </QueryClientProvider>
  )
}

export function useAuthBootstrap() {
  return useContext(AuthBootstrapContext)
}

export function useAuthState() {
  return useSyncExternalStore(subscribeAuth, getAuthState, getAuthState)
}

export function useLogin() {
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      api.login(email, password),
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.logout(),
    onSettled: () => {
      queryClient.clear()
      // Hard replace so Back / bfcache cannot restore the signed-in screen.
      replaceToLogin()
    },
  })
}
