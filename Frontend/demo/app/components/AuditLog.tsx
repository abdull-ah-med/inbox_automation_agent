"use client";

import { AuditEntry } from "../data/sampleEmails";

const eventIcons: Record<string, string> = {
  email_received: "📨",
  classification: "🏷️",
  entities_extracted: "🔍",
  draft_generated: "📝",
  queued_for_review: "📋",
};

const sourceColors: Record<string, string> = {
  system: "text-gray-400",
  agent: "text-blue-500",
  elise: "text-green-500",
};

export default function AuditLog({ entries }: { entries: AuditEntry[] }) {
  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-5">
      <h3 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-4">
        Audit Log
      </h3>

      <div className="space-y-0">
        {entries.map((entry, i) => (
          <div key={i} className="flex gap-3 py-2 border-b border-gray-100 dark:border-gray-800 last:border-0">
            {/* Timeline dot */}
            <div className="flex flex-col items-center pt-0.5">
              <span className="text-sm">{eventIcons[entry.event] || "•"}</span>
              {i < entries.length - 1 && (
                <div className="w-px flex-1 bg-gray-200 dark:bg-gray-700 mt-1" />
              )}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-2">
                <span className="text-xs font-mono text-gray-400">
                  {new Date(entry.timestamp).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </span>
                <span className={`text-xs font-medium uppercase ${sourceColors[entry.source]}`}>
                  {entry.source}
                </span>
              </div>
              <p className="text-sm text-gray-700 dark:text-gray-300 mt-0.5">
                {entry.detail}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
