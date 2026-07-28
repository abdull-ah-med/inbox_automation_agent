import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearAuthSession,
  getAccessToken,
  getAuthState,
  setAuthSession,
} from "@/features/auth/auth-store";

describe("auth-store", () => {
  beforeEach(() => {
    clearAuthSession();
  });

  it("sets and clears in-memory session", () => {
    setAuthSession({
      accessToken: "tok",
      expiresIn: 900,
      user: {
        id: "1",
        email: "elise@example.com",
        role: "user",
        created_at: new Date().toISOString(),
      },
    });
    expect(getAccessToken()).toBe("tok");
    expect(getAuthState().user?.email).toBe("elise@example.com");
    clearAuthSession();
    expect(getAccessToken()).toBeNull();
  });
});

describe("api-client retry-on-401", () => {
  beforeEach(() => {
    clearAuthSession();
    vi.restoreAllMocks();
  });

  it("retries once after refresh on 401", async () => {
    setAuthSession({
      accessToken: "expired",
      expiresIn: 1,
      user: {
        id: "1",
        email: "elise@example.com",
        role: "user",
        created_at: new Date().toISOString(),
      },
    });

    document.cookie = "itr_csrf=nonce.sig";

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "expired" }), { status: 401 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: "fresh",
            token_type: "Bearer",
            expires_in: 900,
            user: {
              id: "1",
              email: "elise@example.com",
              role: "user",
              created_at: new Date().toISOString(),
            },
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            mailboxes: [],
            total_threads: 0,
            total_awaiting: 0,
            total_stale: 0,
            recent_activity: [],
            updated_at: new Date().toISOString(),
          }),
          { status: 200 },
        ),
      );

    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("@/lib/api-client");
    const data = await api.dashboard.overview();
    expect(data.total_threads).toBe(0);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(getAccessToken()).toBe("fresh");
  });
});
