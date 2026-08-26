"use client"

import { useRef, useState } from "react"
import { FileArchive, Upload } from "lucide-react"

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
  SkillDuplicateCandidatesError,
  type ImportSkillOptions,
  type SkillDuplicateCandidate,
} from "@/lib/api-client"
import type { ImportSkillResult } from "@/lib/types"

export const MAX_SKILL_ARCHIVE_BYTES = 10 * 1024 * 1024

const ACCEPTED_EXTENSIONS = [".zip", ".skill"] as const

export const isAcceptedSkillArchive = (file: File): boolean => {
  const lower = file.name.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext))
}

export const validateSkillArchiveClient = (
  file: File,
): string | null => {
  if (!isAcceptedSkillArchive(file)) {
    return "Upload a .zip or .skill archive"
  }
  if (file.size > MAX_SKILL_ARCHIVE_BYTES) {
    return `Archive exceeds ${MAX_SKILL_ARCHIVE_BYTES / (1024 * 1024)} MB limit`
  }
  if (file.size === 0) {
    return "Uploaded file is empty"
  }
  return null
}

type ImportSkillDropzoneProps = {
  disabled?: boolean
  isPending?: boolean
  onImport: (
    file: File,
    options?: ImportSkillOptions,
  ) => Promise<ImportSkillResult>
  onImported?: (result: ImportSkillResult) => void
}

export const ImportSkillDropzone = ({
  disabled = false,
  isPending = false,
  onImport,
  onImported,
}: ImportSkillDropzoneProps) => {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [clientError, setClientError] = useState<string | null>(null)
  const [serverError, setServerError] = useState<string | null>(null)
  const [result, setResult] = useState<ImportSkillResult | null>(null)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [candidates, setCandidates] = useState<SkillDuplicateCandidate[]>([])
  const [dialogOpen, setDialogOpen] = useState(false)
  const [newName, setNewName] = useState("")
  const [retryPending, setRetryPending] = useState(false)

  const handlePickClick = () => {
    if (disabled || isPending || retryPending) return
    inputRef.current?.click()
  }

  const handlePickKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handlePickClick()
    }
  }

  const handleImportSuccess = (imported: ImportSkillResult) => {
    setResult(imported)
    onImported?.(imported)
    setDialogOpen(false)
    setPendingFile(null)
    setCandidates([])
    setNewName("")
  }

  const handleFile = async (
    file: File | null,
    options?: ImportSkillOptions,
  ) => {
    if (!file || disabled || isPending || retryPending) return
    setClientError(null)
    setServerError(null)
    setResult(null)

    const validationError = validateSkillArchiveClient(file)
    if (validationError) {
      setClientError(validationError)
      return
    }

    try {
      const imported = await onImport(file, options)
      handleImportSuccess(imported)
    } catch (error) {
      if (error instanceof SkillDuplicateCandidatesError) {
        setPendingFile(file)
        setCandidates(error.candidates)
        setNewName("")
        setDialogOpen(true)
        return
      }
      const message =
        error instanceof Error && error.message.trim()
          ? error.message
          : "Import failed"
      setServerError(message)
    }
  }

  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null
    event.target.value = ""
    void handleFile(file)
  }

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    if (disabled || isPending || retryPending) return
    setDragging(true)
  }

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
  }

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (disabled || isPending || retryPending) return
    const file = event.dataTransfer.files?.[0] ?? null
    void handleFile(file)
  }

  const handleOverwrite = async (candidateId: string) => {
    if (!pendingFile) return
    setRetryPending(true)
    setServerError(null)
    try {
      const imported = await onImport(pendingFile, {
        overwrite: true,
        overwriteSkillId: candidateId,
      })
      handleImportSuccess(imported)
    } catch (error) {
      const message =
        error instanceof Error && error.message.trim()
          ? error.message
          : "Import failed"
      setServerError(message)
    } finally {
      setRetryPending(false)
    }
  }

  const handleCreateAsNew = async () => {
    if (!pendingFile) return
    const trimmed = newName.trim()
    if (!trimmed) {
      setServerError("Enter a new skill name to create alongside the similar skill")
      return
    }
    setRetryPending(true)
    setServerError(null)
    try {
      const imported = await onImport(pendingFile, {
        overwrite: false,
        nameOverride: trimmed,
      })
      handleImportSuccess(imported)
    } catch (error) {
      const message =
        error instanceof Error && error.message.trim()
          ? error.message
          : "Import failed"
      setServerError(message)
    } finally {
      setRetryPending(false)
    }
  }

  const handleDialogOpenChange = (open: boolean) => {
    if (retryPending) return
    setDialogOpen(open)
    if (!open) {
      setPendingFile(null)
      setCandidates([])
      setNewName("")
    }
  }

  const busy = disabled || isPending || retryPending

  return (
    <div className="space-y-3">
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={[
          "rounded-xl border border-dashed p-6 transition-colors",
          dragging
            ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30"
            : "border-gray-300 bg-gray-50 dark:border-gray-600 dark:bg-gray-950/40",
          busy ? "opacity-60" : "",
        ].join(" ")}
      >
        <div className="flex flex-col items-center gap-3 text-center">
          <FileArchive
            className="size-8 text-muted-foreground"
            aria-hidden="true"
          />
          <div>
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
              Import Claude Skill
            </p>
            <p className="mt-1 text-xs text-gray-500">
              Drop a .zip or .skill archive (max 10 MB). Scripts are skipped —
              this app never executes imported code.
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            tabIndex={0}
            aria-label="Choose skill archive file"
            disabled={busy}
            onClick={handlePickClick}
            onKeyDown={handlePickKeyDown}
          >
            <Upload aria-hidden="true" />
            {isPending || retryPending ? "Importing…" : "Choose file"}
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept=".zip,.skill,application/zip,application/x-zip-compressed"
            className="sr-only"
            aria-label="Skill archive file input"
            disabled={busy}
            onChange={handleInputChange}
          />
        </div>
      </div>

      {clientError ? (
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          {clientError}
        </p>
      ) : null}
      {serverError ? (
        <p className="text-sm text-red-600 dark:text-red-400" role="alert">
          {serverError}
        </p>
      ) : null}

      {result ? (
        <div className="rounded-xl bg-card p-5 ring-1 ring-foreground/10">
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
            Imported {result.name}
            {result.overwritten ? " (overwritten)" : ""}
          </p>
          {result.description ? (
            <p className="mt-1 text-sm text-gray-500">{result.description}</p>
          ) : null}
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
              <p className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
                Assets
              </p>
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
      ) : null}

      <Dialog open={dialogOpen} onOpenChange={handleDialogOpenChange}>
        <DialogContent size="lg" aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>Similar skill found</DialogTitle>
            <DialogDescription>
              This archive looks similar to an existing skill. Overwrite one of
              the matches, or create it as a new skill with a different name.
            </DialogDescription>
          </DialogHeader>

          <ul className="space-y-3">
            {candidates.map((candidate) => (
              <li
                key={candidate.id}
                className="flex flex-col gap-2 border-b border-border pb-3 last:border-b-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
              >
                <div>
                  <p className="text-sm font-medium text-foreground">
                    {candidate.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
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
                    void handleOverwrite(candidate.id)
                  }}
                >
                  Overwrite
                </Button>
              </li>
            ))}
          </ul>

          <div className="space-y-2">
            <label
              htmlFor="skill-name-override"
              className="text-sm font-medium text-foreground"
            >
              Create as new skill
            </label>
            <Input
              id="skill-name-override"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="new-skill-name"
              aria-label="New skill name"
              disabled={retryPending}
            />
            <p className="text-xs text-muted-foreground">
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
              onClick={() => handleDialogOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Create skill with new name"
              disabled={retryPending || !newName.trim()}
              onClick={() => {
                void handleCreateAsNew()
              }}
            >
              Create as new
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
