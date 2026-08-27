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
import type { SkillDuplicateCandidate } from "@/lib/api-client"
import type { ImportSkillResult } from "@/lib/types"

type ImportSkillResultCardProps = {
  result: ImportSkillResult
}

export const ImportSkillResultCard = ({ result }: ImportSkillResultCardProps) => (
  <div className="bg-card ring-foreground/10 rounded-xl p-5 ring-1">
    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
      Imported {result.name}
      {result.overwritten ? " (overwritten)" : ""}
    </p>
    {result.description ? <p className="mt-1 text-sm text-gray-500">{result.description}</p> : null}
    {result.reference_files.length > 0 ? (
      <div className="mt-3">
        <p className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
          Reference files
        </p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-gray-700 dark:text-gray-300">
          {result.reference_files.map((path) => (
            <li key={path}>{path}</li>
          ))}
        </ul>
      </div>
    ) : null}
    {result.asset_files.length > 0 ? (
      <div className="mt-3">
        <p className="text-xs font-semibold tracking-wide text-gray-500 uppercase">Assets</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-gray-700 dark:text-gray-300">
          {result.asset_files.map((path) => (
            <li key={path}>{path}</li>
          ))}
        </ul>
      </div>
    ) : null}
    {result.warnings.length > 0 ? (
      <div className="mt-3">
        <p className="text-xs font-semibold tracking-wide text-amber-700 uppercase dark:text-amber-400">
          Warnings
        </p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-amber-800 dark:text-amber-300">
          {result.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      </div>
    ) : null}
  </div>
)

type DuplicateSkillDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  candidates: SkillDuplicateCandidate[]
  newName: string
  onNewNameChange: (value: string) => void
  retryPending: boolean
  onOverwrite: (candidateId: string) => void
  onCreateAsNew: () => void
}

export const DuplicateSkillDialog = ({
  open,
  onOpenChange,
  candidates,
  newName,
  onNewNameChange,
  retryPending,
  onOverwrite,
  onCreateAsNew,
}: DuplicateSkillDialogProps) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent size="lg" aria-describedby={undefined}>
      <DialogHeader>
        <DialogTitle>Similar skill found</DialogTitle>
        <DialogDescription>
          This archive looks similar to an existing skill. Overwrite one of the matches, or create
          it as a new skill with a different name.
        </DialogDescription>
      </DialogHeader>

      <ul className="space-y-3">
        {candidates.map((candidate) => (
          <li
            key={candidate.id}
            className="border-border flex flex-col gap-2 border-b pb-3 last:border-b-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
          >
            <div>
              <p className="text-foreground text-sm font-medium">{candidate.name}</p>
              <p className="text-muted-foreground text-xs">
                {Math.round(candidate.similarity * 100)}% similar
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              tabIndex={0}
              aria-label={`Overwrite skill ${candidate.name}`}
              disabled={retryPending}
              onClick={() => {
                onOverwrite(candidate.id)
              }}
            >
              Overwrite
            </Button>
          </li>
        ))}
      </ul>

      <div className="space-y-2">
        <label htmlFor="skill-name-override" className="text-foreground text-sm font-medium">
          Create as new skill
        </label>
        <Input
          id="skill-name-override"
          value={newName}
          onChange={(event) => onNewNameChange(event.target.value)}
          placeholder="new-skill-name"
          aria-label="New skill name"
          disabled={retryPending}
        />
        <p className="text-muted-foreground text-xs">
          Lowercase letters, numbers, and hyphens only (max 64 characters).
        </p>
      </div>

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          tabIndex={0}
          aria-label="Cancel duplicate skill prompt"
          disabled={retryPending}
          onClick={() => onOpenChange(false)}
        >
          Cancel
        </Button>
        <Button
          type="button"
          tabIndex={0}
          aria-label="Create skill with new name"
          disabled={retryPending || !newName.trim()}
          onClick={onCreateAsNew}
        >
          Create as new
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)
