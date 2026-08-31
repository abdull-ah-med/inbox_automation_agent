"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Plus } from "lucide-react"

import { ErrorPage } from "@/components/error-page"
import { ImportSkillDropzone } from "@/components/import-skill-dropzone"
import { ImportedSkillViewer } from "@/components/imported-skill-viewer"
import { DeleteSkillDialog, SkillFormDialog } from "@/app/(app)/settings/_sections/skill-dialogs"
import { SkillListItem } from "@/app/(app)/settings/_sections/skill-list-item"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthState } from "@/features/auth/use-auth"
import { api, type ImportSkillOptions } from "@/lib/api-client"
import type { SkillCreate, SkillResponse, SkillUpdate } from "@/lib/types"

const emptyForm = (): SkillCreate => ({
  name: "",
  description: "",
  content: "",
  category: "general",
  always_apply: false,
  is_active: true,
})

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
    mutationFn: ({ id, body }: { id: string; body: SkillUpdate }) => api.skills.update(id, body),
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
    mutationFn: ({ file, options }: { file: File; options?: ImportSkillOptions }) =>
      api.skills.import(file, options),
    onSuccess: async () => {
      await invalidate()
    },
  })

  const handleImportSkill = async (file: File, options?: ImportSkillOptions) => {
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
      category: skill.category ?? "general",
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
          <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Skills</h1>
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
          <Button type="button" tabIndex={0} aria-label="Add skill" onClick={handleOpenCreate}>
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

      <div className="bg-card ring-foreground/10 overflow-hidden rounded-xl ring-1">
        {skills.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">
            No skills yet. Add one to shape how drafts are written.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {skills.map((skill) => (
              <SkillListItem
                key={skill.id}
                skill={skill}
                isAdmin={isAdmin}
                busy={busy}
                onOpenViewer={handleOpenViewer}
                onViewerKeyDown={handleViewerKeyDown}
                onToggle={(item) =>
                  toggleMutation.mutate({
                    id: item.id,
                    is_active: !item.is_active,
                  })
                }
                onEdit={handleOpenEdit}
                onDelete={setDeleteTarget}
              />
            ))}
          </ul>
        )}
      </div>

      <SkillFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        editing={editing}
        form={form}
        onFormChange={setForm}
        formError={formError}
        busy={busy}
        onSave={handleSave}
      />
      <DeleteSkillDialog
        target={deleteTarget}
        onClear={() => setDeleteTarget(null)}
        busy={busy}
        isPending={deleteMutation.isPending}
        onConfirm={(id) => deleteMutation.mutate(id)}
      />
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
