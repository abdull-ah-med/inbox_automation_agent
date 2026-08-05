"use client"

import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { ChevronDown, ChevronRight, Download, FileText } from "lucide-react"

import { EmailBody } from "@/components/email-body"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api-client"
import type { SkillFileMeta, SkillResponse } from "@/lib/types"

const isInlineText = (file: SkillFileMeta): boolean => {
  const path = file.relative_path.toLowerCase()
  if (
    path.endsWith(".md") ||
    path.endsWith(".txt") ||
    path.endsWith(".csv") ||
    path.endsWith(".json") ||
    path.endsWith(".yml") ||
    path.endsWith(".yaml")
  ) {
    return true
  }
  return (
    file.mime_type.startsWith("text/") ||
    file.mime_type === "application/json" ||
    file.mime_type === "text/csv"
  )
}

const SkillFileRow = ({
  skillId,
  file,
}: {
  skillId: string
  file: SkillFileMeta
}) => {
  const [open, setOpen] = useState(false)
  const canInline = isInlineText(file)

  const contentQuery = useQuery({
    queryKey: ["skill-file", skillId, file.relative_path],
    queryFn: async () => {
      const { blob } = await api.skills.getFile(skillId, file.relative_path)
      if (canInline) {
        return { kind: "text" as const, text: await blob.text() }
      }
      return {
        kind: "blob" as const,
        url: URL.createObjectURL(blob),
        mime: file.mime_type,
      }
    },
    enabled: open,
  })

  const handleToggle = () => {
    setOpen((value) => !value)
  }

  const handleToggleKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
  ) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleToggle()
    }
  }

  return (
    <li className="rounded border border-gray-100 dark:border-gray-800">
      <button
        type="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={`Toggle file ${file.relative_path}`}
        onClick={handleToggle}
        onKeyDown={handleToggleKeyDown}
        className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-sm text-gray-800 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800/60"
      >
        {open ? (
          <ChevronDown className="size-4 shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-4 shrink-0" aria-hidden="true" />
        )}
        <FileText className="size-4 shrink-0 text-gray-400" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">{file.relative_path}</span>
        <span className="shrink-0 text-xs text-gray-400">
          {file.kind} · {(file.size_bytes / 1024).toFixed(1)} KB
        </span>
      </button>
      {open ? (
        <div className="border-t border-gray-100 px-3 py-3 dark:border-gray-800">
          {contentQuery.isLoading ? (
            <p className="text-sm text-gray-500">Loading…</p>
          ) : null}
          {contentQuery.isError ? (
            <p className="text-sm text-red-600 dark:text-red-400" role="alert">
              {(contentQuery.error as Error).message}
            </p>
          ) : null}
          {contentQuery.data?.kind === "text" ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-3 text-xs text-gray-800 dark:bg-gray-950 dark:text-gray-200">
              {contentQuery.data.text}
            </pre>
          ) : null}
          {contentQuery.data?.kind === "blob" ? (
            <a
              href={contentQuery.data.url}
              download={file.relative_path.split("/").pop()}
              tabIndex={0}
              aria-label={`Download ${file.relative_path}`}
              className="inline-flex items-center gap-1.5 text-sm text-blue-600 hover:underline dark:text-blue-400"
            >
              <Download className="size-4" aria-hidden="true" />
              Download file
            </a>
          ) : null}
        </div>
      ) : null}
    </li>
  )
}

type ImportedSkillViewerProps = {
  skill: SkillResponse | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export const ImportedSkillViewer = ({
  skill,
  open,
  onOpenChange,
}: ImportedSkillViewerProps) => {
  const filesQuery = useQuery({
    queryKey: ["skill-files", skill?.id],
    queryFn: () => api.skills.listFiles(skill!.id),
    enabled: open && Boolean(skill?.id),
  })

  if (!skill) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{skill.name}</DialogTitle>
          <DialogDescription>
            {skill.description ?? "Imported Claude Skill package"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <p className="mb-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
              SKILL.md
            </p>
            <div className="max-h-72 overflow-auto rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-950">
              <EmailBody text={skill.content} />
            </div>
          </div>

          <div>
            <p className="mb-2 text-xs font-semibold tracking-wide text-gray-500 uppercase">
              Bundled files
            </p>
            {filesQuery.isLoading ? (
              <p className="text-sm text-gray-500">Loading files…</p>
            ) : null}
            {filesQuery.isError ? (
              <p className="text-sm text-red-600 dark:text-red-400" role="alert">
                {(filesQuery.error as Error).message}
              </p>
            ) : null}
            {filesQuery.data && filesQuery.data.length === 0 ? (
              <p className="text-sm text-gray-500">No bundled reference or asset files.</p>
            ) : null}
            {filesQuery.data && filesQuery.data.length > 0 ? (
              <ul className="space-y-2">
                {filesQuery.data.map((file) => (
                  <SkillFileRow
                    key={file.id}
                    skillId={skill.id}
                    file={file}
                  />
                ))}
              </ul>
            ) : null}
          </div>

          <div className="flex justify-end">
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Close skill viewer"
              onClick={() => onOpenChange(false)}
            >
              Close
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
