"use client";

import { Classification } from "../data/sampleEmails";

const urgencyStyles: Record<string, { bg: string; text: string }> = {
  high: { bg: "bg-red-100 dark:bg-red-900/30", text: "text-red-700 dark:text-red-400" },
  medium: { bg: "bg-yellow-100 dark:bg-yellow-900/30", text: "text-yellow-700 dark:text-yellow-400" },
  low: { bg: "bg-green-100 dark:bg-green-900/30", text: "text-green-700 dark:text-green-400" },
};

const intentLabels: Record<string, string> = {
  reply: "↩ Reply",
  forward: "→ Forward",
  "document-request": "📄 Document Request",
  fyi: "ℹ FYI",
};

export default function ClassificationPanel({
  classification,
}: {
  classification: Classification;
}) {
  const urgency = urgencyStyles[classification.urgency];

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-5">
      <h3 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-4">
        Classification
      </h3>

      <div className="space-y-3">
        {/* Type */}
        <div>
          <span className="text-xs text-gray-400">Type</span>
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
            {classification.type}
          </p>
        </div>

        {/* Intent + Urgency row */}
        <div className="flex gap-4">
          <div>
            <span className="text-xs text-gray-400">Intent</span>
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
              {intentLabels[classification.intent]}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-400">Urgency</span>
            <p className={`text-sm font-medium px-2 py-0.5 rounded inline-block ${urgency.bg} ${urgency.text}`}>
              {classification.urgency.toUpperCase()}
            </p>
          </div>
        </div>

        {/* Confidence */}
        <div>
          <span className="text-xs text-gray-400">Confidence</span>
          <div className="flex items-center gap-2 mt-1">
            <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full"
                style={{ width: `${classification.confidence * 100}%` }}
              />
            </div>
            <span className="text-sm font-mono text-gray-600 dark:text-gray-300">
              {(classification.confidence * 100).toFixed(0)}%
            </span>
          </div>
        </div>

        {/* Routed by */}
        <div>
          <span className="text-xs text-gray-400">Routed By</span>
          <p className="text-sm text-gray-900 dark:text-gray-100">
            {classification.routedBy === "rule" ? (
              <span>
                <span className="font-medium">Rule</span>
                {classification.ruleMatch && (
                  <span className="text-xs text-gray-500 dark:text-gray-400 block mt-0.5">
                    {classification.ruleMatch}
                  </span>
                )}
              </span>
            ) : (
              <span className="font-medium">LLM Fallback</span>
            )}
          </p>
        </div>

        {/* Entities */}
        {Object.entries(classification.entities).some(([, v]) => v) && (
          <div>
            <span className="text-xs text-gray-400">Extracted Entities</span>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {Object.entries(classification.entities).map(
                ([key, value]) =>
                  value && (
                    <span
                      key={key}
                      className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 px-2 py-1 rounded"
                    >
                      <span className="text-gray-400">{key}:</span> {value}
                    </span>
                  )
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
