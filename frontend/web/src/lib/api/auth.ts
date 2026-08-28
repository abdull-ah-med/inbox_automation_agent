import { setAuthSession } from "@/features/auth/auth-store"
import {
  ApiError,
  CSRF_COOKIE,
  CSRF_HEADER,
  apiFetch,
  clearServerSession,
  readCookie,
  refreshAccessToken,
} from "@/lib/api/client"
import type { TokenResponse, UserMe } from "@/lib/types"

export const authApi = {
  login(email: string, password: string) {
    return apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then((data) => {
      setAuthSession({
        accessToken: data.access_token,
        expiresIn: data.expires_in,
        user: data.user,
      })
      return data
    })
  },

  async logout() {
    await clearServerSession()
  },

  refresh: refreshAccessToken,

  me() {
    return apiFetch<UserMe>("/auth/me")
  },

  async changePassword(currentPassword: string, newPassword: string) {
    const csrf = readCookie(CSRF_COOKIE)
    if (!csrf) {
      throw new ApiError("Missing CSRF token", 403)
    }
    await apiFetch<void>("/auth/change-password", {
      method: "POST",
      headers: { [CSRF_HEADER]: csrf },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    })
    await clearServerSession()
  },
}
