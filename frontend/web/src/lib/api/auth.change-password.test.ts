import { beforeEach, describe, expect, it, vi } from "vitest"

import { clearAuthSession, setAuthSession } from "@/features/auth/auth-store"

describe("authApi.changePassword", () => {
  beforeEach(() => {
    clearAuthSession()
    vi.restoreAllMocks()
    document.cookie = "itr_csrf=nonce.sig"
    setAuthSession({
      accessToken: "tok",
      expiresIn: 900,
      user: {
        id: "1",
        email: "elise@example.com",
        role: "user",
        created_at: "2026-01-01T00:00:00Z",
      },
    })
  })

  it("clears the server session via logout after a successful password change", async () => {
    // Bug this catches: changePassword only cleared in-memory auth and left
    // the refresh cookie / server session alive until navigate-to-login.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", fetchMock)

    const { authApi } = await import("@/lib/api/auth")
    await authApi.changePassword("old-password-12", "new-password-12")

    const paths = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(paths.some((path) => path.includes("/auth/change-password"))).toBe(true)
    expect(paths.some((path) => path.includes("/auth/logout"))).toBe(true)
    const logoutIndex = paths.findIndex((path) => path.includes("/auth/logout"))
    const changeIndex = paths.findIndex((path) => path.includes("/auth/change-password"))
    expect(logoutIndex).toBeGreaterThan(changeIndex)
  })
})
