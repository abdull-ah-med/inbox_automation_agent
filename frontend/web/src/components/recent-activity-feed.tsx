"use client";

import { formatEventName, formatRelativeTime } from "@/lib/design-tokens";
import type { AuditEntry } from "@/lib/types";

export function RecentActivityFeed({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400">
        No recent activity.
      </p>
    );
  }

  return (
    <ul className="divide-y divide-gray-100 dark:divide-gray-800">
      {entries.slice(0, 12).map((entry, i) => (
        <li key={`${entry.timestamp}-${entry.event}-${i}`} className="py-3 first:pt-0 last:pb-0">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                {formatEventName(entry.event)}
              </p>
              {entry.detail ? (
                <p className="mt-0.5 line-clamp-2 text-xs text-gray-500 dark:text-gray-400">
                  {entry.detail}
                </p>
              ) : null}
            </div>
            <time className="shrink-0 text-xs text-gray-400">
              {formatRelativeTime(entry.timestamp)}
            </time>
          </div>
        </li>
      ))}
    </ul>
  );
}
