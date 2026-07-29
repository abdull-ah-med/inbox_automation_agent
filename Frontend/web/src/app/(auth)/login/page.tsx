"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useSearchParams } from "next/navigation"
import { Suspense, useEffect } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAuthState, useLogin } from "@/features/auth/use-auth"
import { replaceAfterLogin } from "@/lib/auth-navigation"
import { getErrorMessage } from "@/lib/error-messages"

const schema = z.object({
  email: z.string().email("Enter a valid email"),
  password: z.string().min(8, "Password is required"),
})

type FormValues = z.infer<typeof schema>

const LoginForm = () => {
  const login = useLogin()
  const auth = useAuthState()
  const searchParams = useSearchParams()
  const rawNext = searchParams.get("next") || "/dashboard"
  const destination =
    rawNext.startsWith("/") &&
    !rawNext.startsWith("//") &&
    !rawNext.startsWith("/\\")
      ? rawNext
      : "/dashboard"

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  })

  useEffect(() => {
    if (auth.accessToken) {
      // Already signed in — leave /login so Back cannot return here.
      replaceAfterLogin(destination)
    }
  }, [auth.accessToken, destination])

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await login.mutateAsync(values)
      replaceAfterLogin(destination)
    } catch (err) {
      toast.error(getErrorMessage(err))
    }
  })

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <div className="mx-auto grid min-h-screen max-w-5xl lg:grid-cols-2">
        <aside className="flex flex-col justify-between border-b border-gray-200 bg-white px-8 py-10 lg:border-r lg:border-b-0 dark:border-gray-800 dark:bg-gray-900">
          <div>
            <p className="text-xs font-semibold tracking-wide text-blue-600 uppercase">
              Internal tool
            </p>
            <h1 className="mt-3 text-2xl font-semibold text-gray-900 dark:text-gray-100">
              Inbox Triage Automation
            </h1>
            <p className="mt-3 max-w-sm text-sm leading-relaxed text-gray-500 dark:text-gray-400">
              Read-only workspace for classification, drafting, and review.
              Nothing is sent from this app.
            </p>
          </div>
          <ul className="mt-10 space-y-3 text-sm text-gray-600 dark:text-gray-400">
            <li className="flex gap-2">
              <span className="font-medium text-gray-900 dark:text-gray-100">1.</span>
              Review triage outcomes per mailbox
            </li>
            <li className="flex gap-2">
              <span className="font-medium text-gray-900 dark:text-gray-100">2.</span>
              Inspect classification, urgency, and drafts
            </li>
            <li className="flex gap-2">
              <span className="font-medium text-gray-900 dark:text-gray-100">3.</span>
              Keep mailboxes read-only end to end
            </li>
          </ul>
        </aside>

        <main className="flex items-center justify-center px-6 py-12">
          <form
            onSubmit={onSubmit}
            className="w-full max-w-sm space-y-5"
            aria-label="Sign in form"
          >
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                Sign in
              </h2>
              <p className="mt-1 text-sm text-gray-500">
                Invite-only access for your team.
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="email">Work email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                spellCheck={false}
                aria-invalid={Boolean(form.formState.errors.email)}
                {...form.register("email")}
              />
              {form.formState.errors.email ? (
                <p className="text-xs text-red-600" role="alert">
                  {form.formState.errors.email.message}
                </p>
              ) : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                aria-invalid={Boolean(form.formState.errors.password)}
                {...form.register("password")}
              />
              {form.formState.errors.password ? (
                <p className="text-xs text-red-600" role="alert">
                  {form.formState.errors.password.message}
                </p>
              ) : null}
            </div>

            <Button
              type="submit"
              className="w-full bg-blue-600 hover:bg-blue-700"
              disabled={login.isPending}
            >
              {login.isPending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </main>
      </div>
    </div>
  )
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-50 dark:bg-gray-950" />}>
      <LoginForm />
    </Suspense>
  )
}
