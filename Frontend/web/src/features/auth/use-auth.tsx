"use client";

import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import {
  getAuthState,
  subscribeAuth,
} from "@/features/auth/auth-store";
import { api } from "@/lib/api-client";

const AuthBootstrapContext = createContext<{ bootstrapped: boolean }>({
  bootstrapped: false,
});

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  });
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [queryClient] = useState(makeQueryClient);
  const [bootstrapped, setBootstrapped] = useState(false);
  const auth = useSyncExternalStore(subscribeAuth, getAuthState, getAuthState);
  const pathname = usePathname();
  const bootstrappedOnce = useRef(false);

  const silentRefresh = useCallback(async () => {
    // Already have an access token (e.g. just logged in) — skip refresh.
    if (getAuthState().accessToken) {
      setBootstrapped(true);
      return;
    }
    // Login page: do not auto-refresh (avoids 429 spam + redirect loops).
    if (pathname === "/login" || pathname?.startsWith("/login")) {
      setBootstrapped(true);
      return;
    }
    try {
      await api.refresh();
    } finally {
      setBootstrapped(true);
    }
  }, [pathname]);

  useEffect(() => {
    if (bootstrappedOnce.current) return;
    bootstrappedOnce.current = true;
    void silentRefresh();
  }, [silentRefresh]);

  // Proactive refresh 30s before expiry.
  useEffect(() => {
    if (!auth.expiresAt) return;
    const delay = Math.max(auth.expiresAt - Date.now() - 30_000, 5_000);
    const timer = window.setTimeout(() => {
      void api.refresh();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [auth.expiresAt]);

  useEffect(() => {
    const onFocus = () => {
      const { expiresAt, accessToken } = getAuthState();
      if (accessToken && expiresAt && expiresAt < Date.now()) {
        void api.refresh();
      }
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const value = useMemo(() => ({ bootstrapped }), [bootstrapped]);

  return (
    <QueryClientProvider client={queryClient}>
      <AuthBootstrapContext.Provider value={value}>
        {children}
      </AuthBootstrapContext.Provider>
    </QueryClientProvider>
  );
}

export function useAuthBootstrap() {
  return useContext(AuthBootstrapContext);
}

export function useAuthState() {
  return useSyncExternalStore(subscribeAuth, getAuthState, getAuthState);
}

export function useCurrentUser() {
  const auth = useAuthState();
  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.me(),
    enabled: Boolean(auth.accessToken),
    initialData: auth.user ?? undefined,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      api.login(email, password),
    onSuccess: (data) => {
      queryClient.setQueryData(["auth", "me"], data.user);
    },
  });
}

export function useLogout() {
  const router = useRouter();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.logout(),
    onSettled: () => {
      queryClient.clear();
      router.replace("/login");
    },
  });
}
