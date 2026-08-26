"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { FileArchive, Pencil, Plus, Trash2 } from "lucide-react"

import { ErrorPage } from "@/components/error-page"
import { ImportSkillDropzone } from "@/components/import-skill-dropzone"
import { ImportedSkillViewer } from "@/components/imported-skill-viewer"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
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
import { ROUTING_CATEGORIES, type RoutingCategory } from "@/lib/routing"
import type {
  SkillCreate,
  SkillResponse,
  SkillUpdate,
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

export const SkillsSection = () => {
  const auth = useAuthState()
  const isAdmin = auth.user?.role === "admin"
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<SkillResponse | null>(null)
  const [form, setForm] = useState<SkillCreate>(emptyForm())
  const [deleteTarget, setDeleteTarget] = useState<SkillResponse | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [viewerSkill, setViewerSkill] = useState<SkillResponse | null>(null)
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["skills"],
    queryFn: () => api.skills.list(),
  })

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["skills"] })
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

  const busy =
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending ||
    toggleMutation.isPending ||
    importMutation.isPending

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="space-y-4">
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
  return (
    <>
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
