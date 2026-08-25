"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { FileArchive, Pencil, Plus, Trash2 } from "lucide-react"

import { Breadcrumbs } from "@/components/breadcrumbs"
import { ErrorPage } from "@/components/error-page"
import { ImportSkillDropzone } from "@/components/import-skill-dropzone"
import { ImportedSkillViewer } from "@/components/imported-skill-viewer"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthState } from "@/features/auth/use-auth"
import { api, type ImportSkillOptions } from "@/lib/api-client"
import { replaceToLogin } from "@/lib/auth-navigation"
import { ROUTING_CATEGORIES, type RoutingCategory } from "@/lib/routing"
import { formatReviewerDateTime } from "@/lib/dates"
import type {
  ReplyMemoryResponse,
  SkillCandidateResponse,
  SkillCreate,
  SkillResponse,
  SkillUpdate,
  ToneProfileResponse,
} from "@/lib/types"
import { cn, textLinkClass } from "@/lib/utils"

const emptyForm = (): SkillCreate => ({
  name: "",
  description: "",
  content: "",
  category: "general",
  always_apply: false,
  is_active: true,
})

const ROUTING_CATEGORY_ITEMS = ROUTING_CATEGORIES.map((category) => ({
  label: category,
  value: category,
}))

export default function SettingsPage() {
  const auth = useAuthState()
  const isAdmin = auth.user?.role === "admin"
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<SkillResponse | null>(null)
  const [form, setForm] = useState<SkillCreate>(emptyForm())
  const [deleteTarget, setDeleteTarget] = useState<SkillResponse | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [viewerSkill, setViewerSkill] = useState<SkillResponse | null>(null)
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null)

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["skills"],
    queryFn: () => api.skills.list(),
  })

  const {
    data: replyMemory,
    isLoading: replyMemoryLoading,
    isError: replyMemoryError,
    error: replyMemoryErr,
    refetch: refetchReplyMemory,
  } = useQuery({
    queryKey: ["reply-memory"],
    queryFn: () => api.replyMemory.list(),
  })

  const { data: toneProfiles } = useQuery({
    queryKey: ["tone-profiles"],
    queryFn: () => api.toneProfiles.list(),
  })

  const { data: skillCandidates, refetch: refetchCandidates } = useQuery({
    queryKey: ["skill-candidates"],
    queryFn: () => api.skillCandidates.list(),
  })

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["skills"] })
  }

  const invalidateReplyMemory = async () => {
    await queryClient.invalidateQueries({ queryKey: ["reply-memory"] })
  }

  const createMutation = useMutation({
    mutationFn: (body: SkillCreate) => api.skills.create(body),
    onSuccess: async () => {
      setDialogOpen(false)
      setForm(emptyForm())
      setFormError(null)
      await invalidate()
    },
    onError: (err: Error) => setFormError(err.message),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: SkillUpdate }) =>
      api.skills.update(id, body),
    onSuccess: async () => {
      setDialogOpen(false)
      setEditing(null)
      setForm(emptyForm())
      setFormError(null)
      await invalidate()
    },
    onError: (err: Error) => setFormError(err.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.skills.delete(id),
    onSuccess: async () => {
      setDeleteTarget(null)
      await invalidate()
    },
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.skills.update(id, { is_active }),
    onSuccess: async () => {
      await invalidate()
    },
  })

  const excludeMutation = useMutation({
    mutationFn: ({
      id,
      is_excluded,
    }: {
      id: string
      is_excluded: boolean
    }) => api.replyMemory.setExcluded(id, is_excluded),
    onSuccess: async () => {
      await invalidateReplyMemory()
    },
  })

  const importMutation = useMutation({
    mutationFn: ({
      file,
      options,
    }: {
      file: File
      options?: ImportSkillOptions
    }) => api.skills.import(file, options),
    onSuccess: async () => {
      await invalidate()
    },
  })

  const handleImportSkill = async (
    file: File,
    options?: ImportSkillOptions,
  ) => {
    return importMutation.mutateAsync({ file, options })
  }

  const handleOpenViewer = (skill: SkillResponse) => {
    if (skill.source_kind !== "imported") return
    setViewerSkill(skill)
  }

  const handleViewerKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    skill: SkillResponse,
  ) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleOpenViewer(skill)
    }
  }

  const handleOpenCreate = () => {
    if (!isAdmin) return
    setEditing(null)
    setForm(emptyForm())
    setFormError(null)
    setDialogOpen(true)
  }

  const handleOpenEdit = (skill: SkillResponse) => {
    if (!isAdmin) return
    setEditing(skill)
    setForm({
      name: skill.name,
      description: skill.description ?? "",
      content: skill.content,
      category: (skill.category as RoutingCategory | null) ?? "general",
      always_apply: skill.always_apply ?? false,
      is_active: skill.is_active,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  const handleSave = () => {
    if (!isAdmin) return
    if (!form.name.trim() || !form.content.trim()) {
      setFormError("Name and content are required.")
      return
    }
    if (!form.always_apply && !form.category) {
      setFormError("Category is required unless Always apply is enabled.")
      return
    }
    const payload: SkillCreate = {
      name: form.name.trim(),
      description: form.description?.trim() || undefined,
      content: form.content.trim(),
      category: form.always_apply ? form.category || null : form.category,
      always_apply: form.always_apply ?? false,
      is_active: form.is_active ?? true,
    }
    if (editing) {
      updateMutation.mutate({ id: editing.id, body: payload })
      return
    }
    createMutation.mutate(payload)
  }

  const handleToggleExclude = (item: ReplyMemoryResponse) => {
    if (!isAdmin) return
    excludeMutation.mutate({
      id: item.id,
      is_excluded: !item.is_excluded,
    })
  }

  const acceptCandidateMutation = useMutation({
    mutationFn: (id: string) => api.skillCandidates.accept(id),
    onSuccess: async () => {
      await invalidate()
      await refetchCandidates()
    },
  })

  const dismissCandidateMutation = useMutation({
    mutationFn: (id: string) => api.skillCandidates.dismiss(id),
    onSuccess: async () => {
      await refetchCandidates()
    },
  })

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

  const handleAcceptCandidate = (candidate: SkillCandidateResponse) => {
    if (!isAdmin) return
    acceptCandidateMutation.mutate(candidate.id)
  }

  const handleDismissCandidate = (candidate: SkillCandidateResponse) => {
    if (!isAdmin) return
    dismissCandidateMutation.mutate(candidate.id)
  }

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

  const busy =
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending ||
    toggleMutation.isPending ||
    excludeMutation.isPending ||
    importMutation.isPending ||
    changePasswordMutation.isPending

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Breadcrumbs
          items={[
            { label: "Overview", href: "/dashboard" },
            { label: "Settings" },
          ]}
        />
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="space-y-4">
        <Breadcrumbs
          items={[
            { label: "Overview", href: "/dashboard" },
            { label: "Settings" },
          ]}
        />
        <ErrorPage
          error={error}
          onRetry={() => {
            void refetch()
          }}
        />
      </div>
    )
  }

  const skills = data ?? []
  const replies = replyMemory ?? []
  const profiles = toneProfiles ?? []
  const candidates = skillCandidates ?? []

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Overview", href: "/dashboard" },
          { label: "Settings" },
        ]}
      />

      <Card className="mb-8">
        <CardHeader>
          <CardTitle className="text-lg">Account</CardTitle>
          <CardDescription>
            Change your password. You will be signed out afterward and must log in
            again.
          </CardDescription>
          <p className="text-sm text-muted-foreground">
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

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
            Skills
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Standing instructions injected into every draft. Editable without a deploy.
          </p>
          {!isAdmin ? (
            <p className="mt-1 text-sm text-amber-700 dark:text-amber-400">
              View only — an admin account is required to add or change skills.
            </p>
          ) : null}
        </div>
        {isAdmin ? (
          <Button
            type="button"
            tabIndex={0}
            aria-label="Add skill"
            onClick={handleOpenCreate}
          >
            <Plus aria-hidden="true" />
            Add Skill
          </Button>
        ) : null}
      </div>

      {isAdmin ? (
        <div className="mb-5">
          <ImportSkillDropzone
            disabled={!isAdmin}
            isPending={importMutation.isPending}
            onImport={handleImportSkill}
          />
        </div>
      ) : null}

      <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
        {skills.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">
            No skills yet. Add one to shape how drafts are written.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {skills.map((skill) => (
              <li
                key={skill.id}
                className="flex flex-wrap items-start justify-between gap-3 p-4"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    {skill.source_kind === "imported" ? (
                      <button
                        type="button"
                        tabIndex={0}
                        aria-label={`View imported skill ${skill.name}`}
                        onClick={() => handleOpenViewer(skill)}
                        onKeyDown={(event) => handleViewerKeyDown(event, skill)}
                        className={cn(textLinkClass, "font-medium")}
                      >
                        {skill.name}
                      </button>
                    ) : (
                      <p className="font-medium text-gray-900 dark:text-gray-100">
                        {skill.name}
                      </p>
                    )}
                    <StatusBadge
                      label={skill.is_active ? "Active" : "Inactive"}
                      tone={skill.is_active ? "green" : "neutral"}
                    />
                    {skill.source_kind === "imported" ? (
                      <StatusBadge label="Imported" tone="blue" />
                    ) : null}
                    {skill.category ? (
                      <StatusBadge label={skill.category} tone="neutral" />
                    ) : null}
                    {skill.always_apply ? (
                      <StatusBadge label="Always" tone="blue" />
                    ) : null}
                    {skill.source_kind === "imported" &&
                    (skill.reference_file_count > 0 ||
                      skill.asset_file_count > 0) ? (
                      <span className="inline-flex items-center gap-1 text-xs text-gray-500">
                        <FileArchive className="size-3.5" aria-hidden="true" />
                        {skill.reference_file_count} refs
                        {skill.asset_file_count > 0
                          ? ` · ${skill.asset_file_count} assets`
                          : ""}
                      </span>
                    ) : null}
                  </div>
                  {skill.description ? (
                    <p className="text-sm text-gray-500">{skill.description}</p>
                  ) : null}
                  <p className="line-clamp-2 text-sm text-gray-700 dark:text-gray-300">
                    {skill.content}
                  </p>
                </div>
                {isAdmin ? (
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      tabIndex={0}
                      aria-label={
                        skill.is_active
                          ? `Deactivate skill ${skill.name}`
                          : `Activate skill ${skill.name}`
                      }
                      disabled={busy}
                      onClick={() =>
                        toggleMutation.mutate({
                          id: skill.id,
                          is_active: !skill.is_active,
                        })
                      }
                    >
                      {skill.is_active ? "Deactivate" : "Activate"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      tabIndex={0}
                      aria-label={`Edit skill ${skill.name}`}
                      disabled={busy}
                      onClick={() => handleOpenEdit(skill)}
                    >
                      <Pencil aria-hidden="true" />
                      Edit
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="destructive"
                      tabIndex={0}
                      aria-label={`Delete skill ${skill.name}`}
                      disabled={busy}
                      onClick={() => setDeleteTarget(skill)}
                    >
                      <Trash2 aria-hidden="true" />
                      Delete
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <section className="mt-10" aria-labelledby="skill-candidates-heading">
        <div className="mb-5">
          <h2
            id="skill-candidates-heading"
            className="text-xl font-semibold text-gray-900 dark:text-gray-100"
          >
            Proposed skills
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Recurring rejection themes promoted into standing skill drafts for
            review. Accept creates an active skill; dismiss archives the proposal.
          </p>
        </div>
        <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
          {candidates.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">
              No pending proposals. Reject drafts with the same reason a few times
              to surface candidates.
            </p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {candidates.map((candidate) => (
                <li
                  key={candidate.id}
                  className="flex flex-wrap items-start justify-between gap-3 p-4"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium text-gray-900 dark:text-gray-100">
                        {candidate.proposed_name}
                      </p>
                      <StatusBadge label={candidate.routing_category} tone="neutral" />
                      <StatusBadge label={candidate.reason_code} tone="amber" />
                    </div>
                    <p className="text-sm text-gray-700 dark:text-gray-300">
                      {candidate.proposed_content}
                    </p>
                    <p className="text-xs text-gray-500">{candidate.mailbox}</p>
                  </div>
                  {isAdmin ? (
                    <div className="flex shrink-0 flex-wrap gap-2">
                      <Button
                        type="button"
                        size="sm"
                        tabIndex={0}
                        aria-label={`Accept proposed skill ${candidate.proposed_name}`}
                        disabled={
                          acceptCandidateMutation.isPending ||
                          dismissCandidateMutation.isPending
                        }
                        onClick={() => handleAcceptCandidate(candidate)}
                      >
                        Accept
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        tabIndex={0}
                        aria-label={`Dismiss proposed skill ${candidate.proposed_name}`}
                        disabled={
                          acceptCandidateMutation.isPending ||
                          dismissCandidateMutation.isPending
                        }
                        onClick={() => handleDismissCandidate(candidate)}
                      >
                        Dismiss
                      </Button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="mt-10" aria-labelledby="tone-profiles-heading">
        <div className="mb-5">
          <h2
            id="tone-profiles-heading"
            className="text-xl font-semibold text-gray-900 dark:text-gray-100"
          >
            Tone profiles
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Distilled voice rules rebuilt automatically after enough approvals.
            Read-only.
          </p>
        </div>
        <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
          {profiles.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">
              No tone profiles yet. Approve at least 10 drafts to distill a profile.
            </p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {profiles.map((profile: ToneProfileResponse) => (
                <li key={profile.id} className="space-y-2 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium text-gray-900 dark:text-gray-100">
                      {profile.mailbox}
                    </p>
                    <StatusBadge label={profile.routing_category} tone="neutral" />
                    <StatusBadge
                      label={`v${profile.version}`}
                      tone="blue"
                    />
                    <span className="text-xs text-gray-500">
                      {profile.sample_count} samples
                    </span>
                  </div>
                  <p className="text-sm text-gray-700 dark:text-gray-300">
                    {profile.profile.formality} · {profile.profile.typical_length}
                    {profile.profile.greeting_pattern
                      ? ` · ${profile.profile.greeting_pattern}`
                      : ""}
                    {profile.profile.sign_off_pattern
                      ? ` · ${profile.profile.sign_off_pattern}`
                      : ""}
                  </p>
                  {profile.profile.behavioral_rules.length > 0 ? (
                    <ul className="list-disc pl-5 text-sm text-gray-600 dark:text-gray-400">
                      {profile.profile.behavioral_rules.map((rule) => (
                        <li key={rule}>{rule}</li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="mt-10" aria-labelledby="tone-memory-heading">
        <div className="mb-5">
          <h2
            id="tone-memory-heading"
            className="text-xl font-semibold text-gray-900 dark:text-gray-100"
          >
            Tone memory
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Approved replies used as tone references when drafting. Exclude any
            that should not influence future drafts.
          </p>
          {!isAdmin ? (
            <p className="mt-1 text-sm text-amber-700 dark:text-amber-400">
              View only — an admin account is required to exclude tone references.
            </p>
          ) : null}
        </div>

        <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
          {replyMemoryLoading ? (
            <div className="space-y-3 p-4">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : replyMemoryError ? (
            <div className="p-4">
              <ErrorPage
                error={replyMemoryErr}
                onRetry={() => {
                  void refetchReplyMemory()
                }}
              />
            </div>
          ) : replies.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">
              No approved replies yet. Approve a draft to start building tone
              memory.
            </p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {replies.map((item) => (
                <li
                  key={item.id}
                  className="flex flex-wrap items-start justify-between gap-3 p-4"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge
                        label={item.is_excluded ? "Excluded" : "Included"}
                        tone={item.is_excluded ? "neutral" : "green"}
                      />
                      <StatusBadge label={item.mailbox} tone="neutral" />
                      <span className="text-xs text-gray-500">
                        Approved {formatReviewerDateTime(item.created_at)}
                      </span>
                    </div>
                    {item.original_email_preview ? (
                      <p className="text-sm text-gray-500">
                        Re: {item.original_email_preview}
                      </p>
                    ) : null}
                    <p className="line-clamp-3 whitespace-pre-wrap text-sm text-gray-700 dark:text-gray-300">
                      {item.reply_text}
                    </p>
                  </div>
                  {isAdmin ? (
                    <div className="flex shrink-0">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        tabIndex={0}
                        aria-label={
                          item.is_excluded
                            ? "Include this reply in tone memory"
                            : "Exclude this reply from tone memory"
                        }
                        disabled={excludeMutation.isPending}
                        onClick={() => handleToggleExclude(item)}
                      >
                        {item.is_excluded ? "Include" : "Exclude"}
                      </Button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit skill" : "Add skill"}</DialogTitle>
            <DialogDescription>
              Skills are standing instructions the AI follows when drafting replies.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500" htmlFor="skill-name">
                Name
              </label>
              <Input
                id="skill-name"
                value={form.name}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, name: event.target.value }))
                }
                aria-label="Skill name"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500" htmlFor="skill-category">
                Category
              </label>
              <Select
                items={ROUTING_CATEGORY_ITEMS}
                value={form.category ?? "general"}
                onValueChange={(value) => {
                  if (!value) return
                  setForm((prev) => ({
                    ...prev,
                    category: value as RoutingCategory,
                  }))
                }}
                disabled={form.always_apply}
              >
                <SelectTrigger
                  id="skill-category"
                  aria-label="Skill category"
                  className="mt-1 w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {ROUTING_CATEGORY_ITEMS.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs text-gray-500" htmlFor="skill-description">
                Description (optional)
              </label>
              <Input
                id="skill-description"
                value={form.description ?? ""}
                onChange={(event) =>
                  setForm((prev) => ({
                    ...prev,
                    description: event.target.value,
                  }))
                }
                aria-label="Skill description"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500" htmlFor="skill-content">
                Content
              </label>
              <textarea
                id="skill-content"
                value={form.content}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, content: event.target.value }))
                }
                aria-label="Skill content"
                rows={6}
                className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={form.always_apply ?? false}
                onChange={(event) =>
                  setForm((prev) => ({
                    ...prev,
                    always_apply: event.target.checked,
                  }))
                }
                aria-label="Skill always apply"
              />
              Always apply
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={form.is_active ?? true}
                onChange={(event) =>
                  setForm((prev) => ({
                    ...prev,
                    is_active: event.target.checked,
                  }))
                }
                aria-label="Skill active"
              />
              Active
            </label>
            {formError ? (
              <p className="text-sm text-red-600" role="alert">
                {formError}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel skill form"
              onClick={() => setDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Save skill"
              disabled={busy}
              onClick={handleSave}
            >
              {busy ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={deleteTarget != null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete skill</DialogTitle>
            <DialogDescription>
              Delete &quot;{deleteTarget?.name}&quot;? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel delete skill"
              onClick={() => setDeleteTarget(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              tabIndex={0}
              aria-label="Confirm delete skill"
              disabled={busy || !deleteTarget}
              onClick={() => {
                if (deleteTarget) deleteMutation.mutate(deleteTarget.id)
              }}
            >
              {deleteMutation.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ImportedSkillViewer
        skill={viewerSkill}
        open={viewerSkill != null}
        onOpenChange={(open) => {
          if (!open) setViewerSkill(null)
        }}
      />
    </>
  )
}
