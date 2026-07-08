"use client";

import { TriageResult } from "../data/sampleEmails";

export default function SlackPreview({ result }: { result: TriageResult }) {
  const { email, classification, suggestedAction, threadState } = result;

  const urgencyEmoji =
    classification.urgency === "high"
      ? "🔴"
      : classification.urgency === "medium"
      ? "🟡"
      : "🟢";

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-5">
      <h3 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-4">
        Slack Review Queue
      </h3>

      {/* Simulated Slack message */}
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
        {/* Slack header bar */}
        <div className="border-l-4 border-blue-500 px-4 py-3">
          {/* Channel + bot info */}
          <div className="flex items-center gap-2 mb-3">
            <span className="w-5 h-5 rounded bg-blue-500 flex items-center justify-center text-white text-xs font-bold">
              T
            </span>
            <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Triage Bot
            </span>
            <span className="text-xs text-gray-400">
              {new Date(email.receivedAt).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
          </div>

          {/* Thread summary */}
          <div className="mb-3">
            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {urgencyEmoji} {classification.type} — {email.subject}
            </p>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              From: {email.from} → {email.inbox} inbox
            </p>
          </div>

          {/* Quick info row */}
          <div className="flex flex-wrap gap-3 text-xs mb-3">
            <span className="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded text-gray-600 dark:text-gray-300">
              Intent: {suggestedAction.action === "forward" ? `Forward to ${suggestedAction.forwardTo}` : "Reply"}
            </span>
            <span className="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded text-gray-600 dark:text-gray-300">
              Status: {threadState.status.replace(/-/g, " ")}
            </span>
            <span className="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded text-gray-600 dark:text-gray-300">
              Confidence: {(classification.confidence * 100).toFixed(0)}%
            </span>
          </div>

          {/* Draft preview */}
          <div className="bg-gray-50 dark:bg-gray-800 rounded p-3 mb-3">
            <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              📝 Suggested {suggestedAction.action === "forward" ? "Forward" : "Reply"}
            </p>
            <pre className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-sans">
              {suggestedAction.draft}
            </pre>
          </div>

          {/* Teaching note */}
          <div className="bg-blue-50 dark:bg-blue-950/30 rounded p-3">
            <p className="text-xs font-medium text-blue-600 dark:text-blue-400 mb-1">
              📚 Teaching Note
            </p>
            <div className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
              {suggestedAction.teachingNote.split("\n").map((line, i) => {
                if (line.startsWith("**") && line.includes(":**")) {
                  const [label, ...rest] = line.split(":**");
                  return (
                    <p key={i} className="mt-2 first:mt-0">
                      <strong className="text-gray-900 dark:text-gray-100">
                        {label.replace(/\*\*/g, "")}:
                      </strong>
                      {rest.join(":**")}
                    </p>
                  );
                }
                return (
                  <p key={i} className={line.trim() === "" ? "h-2" : ""}>
                    {line}
                  </p>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
