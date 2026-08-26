import { Panel } from "@/components/thread-triage/panel"
import { formatEventName, formatRelativeTime } from "@/lib/design-tokens"
import type { AuditEntry } from "@/lib/types"

type AuditSectionProps = {
  auditLog: AuditEntry[]
}

export const AuditSection = ({ auditLog }: AuditSectionProps) => {
  return (
    <Panel title="Audit log">
      <ul className="space-y-2">
        {auditLog.length === 0 ? (
          <li className="text-sm text-gray-500">No audit events.</li>
        ) : (
          auditLog.map((entry) => (
            <li
              key={`${entry.timestamp}|${entry.event}|${entry.source}|${entry.detail}`}
              className="rounded-xl bg-muted/40 p-3 ring-1 ring-foreground/10"
            >
              <div className="flex justify-between gap-2">
                <span className="text-xs font-medium">
                  {formatEventName(entry.event)}
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatRelativeTime(entry.timestamp)}
                </span>
              </div>
              {entry.detail ? (
                <p className="mt-1 line-clamp-3 text-xs text-gray-500">
                  {entry.detail}
                </p>
              ) : null}
            </li>
          ))
        )}
      </ul>
    </Panel>
  )
}
