"use client"

import { FileArchive, Pencil, Trash2 } from "lucide-react"

import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import type { SkillResponse } from "@/lib/types"
import { cn, textLinkClass } from "@/lib/utils"

type SkillListItemProps = {
  skill: SkillResponse
  isAdmin: boolean
  busy: boolean
  onOpenViewer: (skill: SkillResponse) => void
  onViewerKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>, skill: SkillResponse) => void
  onToggle: (skill: SkillResponse) => void
  onEdit: (skill: SkillResponse) => void
  onDelete: (skill: SkillResponse) => void
}

export const SkillListItem = ({
  skill,
  isAdmin,
  busy,
  onOpenViewer,
  onViewerKeyDown,
  onToggle,
  onEdit,
  onDelete,
}: SkillListItemProps) => (
  <li className="flex flex-wrap items-start justify-between gap-3 p-4">
    <div className="min-w-0 flex-1 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        {skill.source_kind === "imported" ? (
          <button
            type="button"
            tabIndex={0}
            aria-label={`View imported skill ${skill.name}`}
            onClick={() => onOpenViewer(skill)}
            onKeyDown={(event) => onViewerKeyDown(event, skill)}
            className={cn(textLinkClass, "font-medium")}
          >
            {skill.name}
          </button>
        ) : (
          <p className="font-medium text-gray-900 dark:text-gray-100">{skill.name}</p>
        )}
        <StatusBadge
          label={skill.is_active ? "Active" : "Inactive"}
          tone={skill.is_active ? "green" : "neutral"}
        />
        {skill.source_kind === "imported" ? <StatusBadge label="Imported" tone="blue" /> : null}
        {skill.category ? <StatusBadge label={skill.category} tone="neutral" /> : null}
        {skill.always_apply ? <StatusBadge label="Always" tone="blue" /> : null}
        {skill.source_kind === "imported" &&
        (skill.reference_file_count > 0 || skill.asset_file_count > 0) ? (
          <span className="inline-flex items-center gap-1 text-xs text-gray-500">
            <FileArchive className="size-3.5" aria-hidden="true" />
            {skill.reference_file_count} refs
            {skill.asset_file_count > 0 ? ` · ${skill.asset_file_count} assets` : ""}
          </span>
        ) : null}
      </div>
      {skill.description ? <p className="text-sm text-gray-500">{skill.description}</p> : null}
      <p className="line-clamp-2 text-sm text-gray-700 dark:text-gray-300">{skill.content}</p>
    </div>
    {isAdmin ? (
      <div className="flex shrink-0 flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          tabIndex={0}
          aria-label={
            skill.is_active ? `Deactivate skill ${skill.name}` : `Activate skill ${skill.name}`
          }
          disabled={busy}
          onClick={() => onToggle(skill)}
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
          onClick={() => onEdit(skill)}
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
          onClick={() => onDelete(skill)}
        >
          <Trash2 aria-hidden="true" />
          Delete
        </Button>
      </div>
    ) : null}
  </li>
)
