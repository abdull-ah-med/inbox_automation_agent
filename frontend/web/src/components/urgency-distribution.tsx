"use client";

import { inboxColor, inboxLabel } from "@/lib/design-tokens";
import type { MailboxOverview } from "@/lib/types";

const LEVELS = ["CRITICAL", "HIGH", "NORMAL", "LOW"] as const;

export function UrgencyDistribution({
  mailboxes,
}: {
  mailboxes: MailboxOverview[];
}) {
  return (
    <div className="space-y-4">
      {mailboxes.map((m) => {
        const total = LEVELS.reduce(
          (sum, level) => sum + (m.urgency_breakdown[level] ?? 0),
          0,
        );
        const label = m.label || inboxLabel(m.mailbox);
        const accent = inboxColor(m.mailbox);
        return (
          <div key={m.mailbox}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="font-medium text-gray-700 dark:text-gray-300">
                {label}
              </span>
              <span className="text-gray-400">{total}</span>
            </div>
            {total > 0 ? (
              <div
                className="flex h-2 overflow-hidden rounded bg-gray-100 dark:bg-gray-800"
                role="img"
                aria-label={`${label}: ${LEVELS.map((level) => `${level} ${m.urgency_breakdown[level] ?? 0}`).join(", ")}`}
              >
                {LEVELS.map((level) => {
                  const count = m.urgency_breakdown[level] ?? 0;
                  if (!count) return null;
                  const color =
                    level === "CRITICAL"
                      ? "#dc2626"
                      : level === "HIGH"
                        ? accent
                        : level === "NORMAL"
                          ? "#9ca3af"
                          : "#d1d5db";
                  return (
                    <div
                      key={level}
                      style={{
                        width: `${(count / total) * 100}%`,
                        backgroundColor: color,
                      }}
                      title={`${level}: ${count}`}
                    />
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">
                No urgent threads right now.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
