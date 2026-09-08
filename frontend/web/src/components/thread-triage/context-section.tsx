"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import { Panel } from "@/components/thread-triage/panel"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api-client"
import { ApiError } from "@/lib/api/client"
import { formatReviewerDateTime } from "@/lib/dates"
import type { ThreadContextFact, ThreadContextView } from "@/lib/types"

const sortFactsNewestFirst = (facts: ThreadContextFact[]): ThreadContextFact[] =>
  facts.toSorted((left, right) => {
    const leftAt = left.source_received_at ?? left.created_at
    const rightAt = right.source_received_at ?? right.created_at
    if (!leftAt && !rightAt) return 0
    if (!leftAt) return 1
    if (!rightAt) return -1
    return new Date(rightAt).getTime() - new Date(leftAt).getTime()
  })

export const ContextSection = ({ threadId }: { threadId: string }) => (
  <ContextSectionEditor key={threadId} threadId={threadId} />
)

const ContextSectionEditor = ({ threadId }: { threadId: string }) => {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ["thread", threadId, "context"],
    queryFn: () => api.threads.getContext(threadId),
    refetchInterval: (current) => (current.state.data?.rebuild_in_progress ? 1500 : false),
  })
  const [override, setOverride] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)

  const savedNotes = query.data?.user_notes ?? ""
  const draftNotes = override ?? savedNotes
  const isDirty = override !== null && override !== savedNotes
  const rebuilding = Boolean(query.data?.rebuild_in_progress)
  const startedInitialExtract = useRef(false)

  const saveMutation = useMutation({
    mutationFn: () =>
      api.threads.saveUserNotes(threadId, {
        user_notes: draftNotes,
        expected_version: query.data?.version ?? 0,
      }),
    onSuccess: (data) => {
      setConflict(false)
      setOverride(null)
      queryClient.setQueryData(["thread", threadId, "context"], data)
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true)
        await queryClient.invalidateQueries({ queryKey: ["thread", threadId, "context"] })
      }
    },
  })

  const rebuildMutation = useMutation({
    mutationFn: () => api.threads.rebuildContext(threadId),
    onSuccess: (data) => {
      queryClient.setQueryData(["thread", threadId, "context"], data)
    },
  })

  const discardFactMutation = useMutation({
    mutationFn: (factId: string) => api.threads.discardContextFact(threadId, factId),
    onSuccess: (data) => {
      queryClient.setQueryData(["thread", threadId, "context"], data)
    },
  })

  const handleSave = () => {
    saveMutation.mutate()
  }
  const handleDiscardNotes = () => {
    setOverride(null)
    setConflict(false)
  }
  const handleNotesChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    setOverride(event.target.value)
  }
  const handleNotesKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "s") {
      event.preventDefault()
      if (isDirty) handleSave()
    }
  }
  const handleRebuild = () => {
    if (isDirty || rebuilding) return
    rebuildMutation.reset()
    rebuildMutation.mutate()
  }
  const handleDiscardFact = (factId: string) => {
    if (rebuilding || rebuildMutation.isPending) return
    discardFactMutation.mutate(factId)
  }

  useEffect(() => {
    if (startedInitialExtract.current) return
    if (!query.data?.needs_initial_extract) return
    if (query.data.rebuild_in_progress) return
    if (rebuildMutation.isError) return
    startedInitialExtract.current = true
    rebuildMutation.mutate()
  }, [query.data?.needs_initial_extract, query.data?.rebuild_in_progress, rebuildMutation])

  if (!query.data) {
    return (
      <Panel title="Context">
        <p className="text-muted-foreground text-sm">Loading context…</p>
      </Panel>
    )
  }

  const waitingForFirstExtract = Boolean(
    query.data.needs_initial_extract && !rebuildMutation.isError,
  )
  const extractPending = rebuilding || rebuildMutation.isPending || waitingForFirstExtract
  const rebuildError =
    query.data.rebuild_error || (rebuildMutation.isError ? rebuildMutation.error.message : null)

  return (
    <Panel title="Context">
      <div className="space-y-4">
        <ContextPinsEditor
          draftNotes={draftNotes}
          conflict={conflict}
          isDirty={isDirty}
          saving={saveMutation.isPending}
          onNotesChange={handleNotesChange}
          onNotesKeyDown={handleNotesKeyDown}
          onSave={handleSave}
          onDiscard={handleDiscardNotes}
        />
        <ContextFactsPanel
          view={query.data}
          extractPending={extractPending}
          rebuildError={rebuildError}
          factsBusy={extractPending || discardFactMutation.isPending}
          rebuildDisabled={isDirty || extractPending}
          onDiscardFact={handleDiscardFact}
          onRebuild={handleRebuild}
        />
      </div>
    </Panel>
  )
}

const ContextPinsEditor = ({
  draftNotes,
  conflict,
  isDirty,
  saving,
  onNotesChange,
  onNotesKeyDown,
  onSave,
  onDiscard,
}: {
  draftNotes: string
  conflict: boolean
  isDirty: boolean
  saving: boolean
  onNotesChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => void
  onNotesKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void
  onSave: () => void
  onDiscard: () => void
}) => (
  <div>
    <div className="flex items-baseline gap-1.5">
      <label htmlFor="thread-context-user-notes" className="text-muted-foreground text-xs">
        Pins
      </label>
      <span className="text-muted-foreground/80 text-[11px]">
        Standing instructions the next draft should follow
      </span>
    </div>
    <Textarea
      id="thread-context-user-notes"
      aria-label="Thread context user notes"
      value={draftNotes}
      onChange={onNotesChange}
      onKeyDown={onNotesKeyDown}
      rows={6}
      className="mt-1"
    />
    {conflict ? (
      <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-300">
        Someone else saved these notes. Reload and try again.
      </p>
    ) : null}
    {isDirty ? (
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          type="button"
          tabIndex={0}
          aria-label="Save notes"
          disabled={saving}
          onClick={onSave}
        >
          {saving ? "Saving…" : "Save notes"}
        </Button>
        <Button
          type="button"
          variant="outline"
          tabIndex={0}
          aria-label="Discard notes"
          onClick={onDiscard}
        >
          Discard
        </Button>
      </div>
    ) : null}
  </div>
)

const ContextFactsPanel = ({
  view,
  extractPending,
  rebuildError,
  factsBusy,
  rebuildDisabled,
  onDiscardFact,
  onRebuild,
}: {
  view: ThreadContextView
  extractPending: boolean
  rebuildError: string | null
  factsBusy: boolean
  rebuildDisabled: boolean
  onDiscardFact: (factId: string) => void
  onRebuild: () => void
}) => {
  const facts = sortFactsNewestFirst(view.facts)

  return (
    <>
      <div>
        <p className="text-muted-foreground text-xs">Facts</p>
        {extractPending ? (
          <p role="status" aria-live="polite" className="text-muted-foreground mt-1 text-sm">
            Reading the whole thread and extracting facts.
          </p>
        ) : null}
        {rebuildError && !extractPending ? (
          <p role="alert" className="mt-1 text-sm text-red-700 dark:text-red-300">
            {rebuildError}
          </p>
        ) : null}
        {!extractPending && view.facts.length === 0 ? (
          <p className="text-muted-foreground mt-1 text-sm italic">
            No useful facts in this thread.
          </p>
        ) : null}
        {facts.length > 0 ? (
          <ul className="mt-2 list-disc space-y-2 pl-5">
            {facts.map((fact) => (
              <ContextFactRow
                key={fact.id}
                fact={fact}
                disabled={factsBusy}
                onDiscard={onDiscardFact}
              />
            ))}
          </ul>
        ) : null}
      </div>
      <Button
        type="button"
        variant="outline"
        tabIndex={0}
        aria-label="Rebuild facts"
        aria-busy={extractPending}
        disabled={rebuildDisabled}
        onClick={onRebuild}
      >
        {extractPending ? "Rebuilding…" : "Rebuild facts"}
      </Button>
    </>
  )
}

const ContextFactRow = ({
  fact,
  disabled,
  onDiscard,
}: {
  fact: ThreadContextFact
  disabled: boolean
  onDiscard: (factId: string) => void
}) => {
  const handleDiscard = () => {
    onDiscard(fact.id)
  }
  return (
    <li className="text-sm text-gray-900 dark:text-gray-100">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p>{fact.body}</p>
          {fact.source_received_at ? (
            <time
              dateTime={fact.source_received_at}
              className="text-muted-foreground mt-0.5 block text-[11px]"
            >
              {formatReviewerDateTime(fact.source_received_at)}
            </time>
          ) : null}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          tabIndex={0}
          aria-label={`Discard fact ${fact.body}`}
          disabled={disabled}
          onClick={handleDiscard}
        >
          Discard
        </Button>
      </div>
    </li>
  )
}
