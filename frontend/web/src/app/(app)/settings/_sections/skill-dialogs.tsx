"use client"

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
import { ROUTING_CATEGORIES } from "@/lib/routing"
import type { SkillCreate, SkillResponse } from "@/lib/types"

const ROUTING_CATEGORY_ITEMS = ROUTING_CATEGORIES.map((category) => ({
  label: category,
  value: category,
}))

type SkillFormDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  editing: SkillResponse | null
  form: SkillCreate
  onFormChange: (updater: (prev: SkillCreate) => SkillCreate) => void
  formError: string | null
  busy: boolean
  onSave: () => void
}

export const SkillFormDialog = ({
  open,
  onOpenChange,
  editing,
  form,
  onFormChange,
  formError,
  busy,
  onSave,
}: SkillFormDialogProps) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
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
            onChange={(event) => onFormChange((prev) => ({ ...prev, name: event.target.value }))}
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
              onFormChange((prev) => ({
                ...prev,
                category: value,
              }))
            }}
            disabled={form.always_apply}
          >
            <SelectTrigger id="skill-category" aria-label="Skill category" className="mt-1 w-full">
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
              onFormChange((prev) => ({
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
            onChange={(event) => onFormChange((prev) => ({ ...prev, content: event.target.value }))}
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
              onFormChange((prev) => ({
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
              onFormChange((prev) => ({
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
          onClick={() => onOpenChange(false)}
        >
          Cancel
        </Button>
        <Button type="button" tabIndex={0} aria-label="Save skill" disabled={busy} onClick={onSave}>
          {busy ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)

type DeleteSkillDialogProps = {
  target: SkillResponse | null
  onClear: () => void
  busy: boolean
  isPending: boolean
  onConfirm: (id: string) => void
}

export const DeleteSkillDialog = ({
  target,
  onClear,
  busy,
  isPending,
  onConfirm,
}: DeleteSkillDialogProps) => (
  <Dialog
    open={target != null}
    onOpenChange={(open) => {
      if (!open) onClear()
    }}
  >
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Delete skill</DialogTitle>
        <DialogDescription>
          Delete &quot;{target?.name}&quot;? This cannot be undone.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          tabIndex={0}
          aria-label="Cancel delete skill"
          onClick={onClear}
        >
          Cancel
        </Button>
        <Button
          type="button"
          variant="destructive"
          tabIndex={0}
          aria-label="Confirm delete skill"
          disabled={busy || !target}
          onClick={() => {
            if (target) onConfirm(target.id)
          }}
        >
          {isPending ? "Deleting…" : "Delete"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)
