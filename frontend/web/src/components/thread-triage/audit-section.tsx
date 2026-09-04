"use client"

import { useState } from "react"

import { Panel } from "@/components/thread-triage/panel"
import { formatEventName, formatRelativeTime } from "@/lib/design-tokens"
import type { AuditEntry } from "@/lib/types"

export const AUDIT_PREVIEW_COUNT = 7

const AuditEventItem = ({ entry }: { entry: AuditEntry }) => (
  <li className="bg-muted/40 ring-foreground/10 rounded-xl p-3 ring-1">
    <div className="flex justify-between gap-2">
      <span className="text-xs font-medium">{formatEventName(entry.event)}</span>
      <span className="text-muted-foreground text-xs">{formatRelativeTime(entry.timestamp)}</span>
    </div>
    {entry.detail ? (
      <p className="mt-1 line-clamp-3 text-xs text-gray-500">{entry.detail}</p>
    ) : null}
  </li>
)

const newestFirst = (auditLog: AuditEntry[]) =>
  auditLog.toSorted((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp))

export const AuditSection = ({ auditLog }: { auditLog: AuditEntry[] }) => {
  const [earlierOpen, setEarlierOpen] = useState(false)
  const eventsNewestFirst = newestFirst(auditLog)
  const hiddenCount = Math.max(0, eventsNewestFirst.length - AUDIT_PREVIEW_COUNT)
  const recent = eventsNewestFirst.slice(0, AUDIT_PREVIEW_COUNT)
  const earlier = hiddenCount > 0 ? eventsNewestFirst.slice(AUDIT_PREVIEW_COUNT) : []

  const handleToggleEarlier = () => {
    setEarlierOpen((open) => !open)
  }

  return (
    <Panel title="Audit log">
      {eventsNewestFirst.length === 0 ? (
        <p className="text-muted-foreground text-sm">No audit events.</p>
      ) : (
        <ul className="space-y-2" aria-label="Thread audit log">
          {recent.map((entry) => (
            <AuditEventItem
              key={`${entry.timestamp}|${entry.event}|${entry.source}|${entry.detail}`}
              entry={entry}
            />
          ))}
          {hiddenCount > 0 ? (
            <li className="flex justify-start py-1">
              <button
                type="button"
                tabIndex={0}
                aria-expanded={earlierOpen}
                aria-label={
                  earlierOpen
                    ? "Hide earlier events"
                    : `Show ${hiddenCount} earlier ${hiddenCount === 1 ? "event" : "events"}`
                }
                className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 rounded-md px-1.5 py-0.5 text-xs tracking-wide transition-colors focus-visible:ring-2 focus-visible:outline-none"
                onClick={handleToggleEarlier}
              >
                {earlierOpen
                  ? "Hide earlier"
                  : `${hiddenCount} earlier ${hiddenCount === 1 ? "event" : "events"}`}
              </button>
            </li>
          ) : null}
          {earlierOpen
            ? earlier.map((entry) => (
                <AuditEventItem
                  key={`${entry.timestamp}|${entry.event}|${entry.source}|${entry.detail}`}
                  entry={entry}
                />
              ))
            : null}
        </ul>
      )}
    </Panel>
  )
}
