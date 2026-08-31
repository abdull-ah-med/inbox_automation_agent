"use client"

import { useMutation } from "@tanstack/react-query"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAuthState } from "@/features/auth/use-auth"
import { api } from "@/lib/api-client"
import { replaceToLogin } from "@/lib/auth-navigation"

export const AccountSection = () => {
  const auth = useAuthState()
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null)

  const changePasswordMutation = useMutation({
    mutationFn: () => api.changePassword(currentPassword, newPassword),
    onSuccess: () => {
      setPasswordError(null)
      setPasswordSuccess("Password updated. Sign in again with your new password.")
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
      window.setTimeout(() => {
        replaceToLogin()
      }, 1200)
    },
    onError: (err: Error) => {
      setPasswordSuccess(null)
      setPasswordError(err.message)
    },
  })

  const handleChangePassword = () => {
    setPasswordError(null)
    setPasswordSuccess(null)
    if (currentPassword.length < 8) {
      setPasswordError("Current password is required.")
      return
    }
    if (newPassword.length < 12) {
      setPasswordError("New password must be at least 12 characters.")
      return
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("New password and confirmation do not match.")
      return
    }
    changePasswordMutation.mutate()
  }

  return (
    <Card className="mb-8">
      <CardHeader>
        <CardTitle className="text-lg">Account</CardTitle>
        <CardDescription>
          Change your password. You will be signed out afterward and must log in again.
        </CardDescription>
        <p className="text-muted-foreground text-sm">
          Signed in as {auth.user?.email ?? "unknown"}
        </p>
      </CardHeader>
      <CardContent>
        <form
          className="mt-4 grid max-w-md gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            handleChangePassword()
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="current-password">Current password</Label>
            <Input
              id="current-password"
              type="password"
              name="current_password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              aria-label="Current password"
              disabled={changePasswordMutation.isPending}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-password">New password</Label>
            <Input
              id="new-password"
              type="password"
              name="new_password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              aria-label="New password"
              disabled={changePasswordMutation.isPending}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="confirm-password">Confirm new password</Label>
            <Input
              id="confirm-password"
              type="password"
              name="confirm_password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              aria-label="Confirm new password"
              disabled={changePasswordMutation.isPending}
            />
          </div>
          {passwordError ? (
            <p className="text-sm text-red-600 dark:text-red-400" role="alert">
              {passwordError}
            </p>
          ) : null}
          {passwordSuccess ? (
            <p className="text-sm text-green-700 dark:text-green-400" role="status">
              {passwordSuccess}
            </p>
          ) : null}
          <div>
            <Button
              type="submit"
              tabIndex={0}
              aria-label="Update password"
              disabled={changePasswordMutation.isPending}
            >
              {changePasswordMutation.isPending ? "Updating…" : "Update password"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
