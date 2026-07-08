"use client";

import { ThreadState } from "../data/sampleEmails";

const stateLabels: Record<string, string> = {
  new: "New",
  "awaiting-client": "Awaiting Client",
  "awaiting-vendor": "Awaiting Vendor",
  "awaiting-partner-services": "Awaiting Partner Services",
  resolved: "Resolved",
};

const stateOrder = [
  "new",
  "awaiting-client",
  "awaiting-vendor",
  "awaiting-partner-services",
  "resolved",
];

export default function ThreadStatePanel({ state }: { state: ThreadState }) {
  const activeIndex = stateOrder.indexOf(state.status);

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-5">
      <h3 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-4">
        Thread State
      </h3>

      {/* State machine */}
      <div className="flex items-center gap-1 mb-4 overflow-x-auto">
        {stateOrder.map((s, i) => (
          <div key={s} className="flex items-center">
            <div
              className={`text-xs px-2.5 py-1 rounded-full whitespace-nowrap ${
                i === activeIndex
                  ? "bg-blue-500 text-white font-medium"
                  : i < activeIndex
                  ? "bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400"
                  : "bg-gray-100 dark:bg-gray-800 text-gray-400"
              }`}
            >
              {stateLabels[s]}
            </div>
            {i < stateOrder.length - 1 && (
              <span className="text-gray-300 dark:text-gray-600 mx-1">→</span>
            )}
          </div>
        ))}
      </div>

      {/* Details */}
      <div className="flex gap-6 text-sm">
        <div>
          <span className="text-xs text-gray-400">Last Updated</span>
          <p className="text-gray-700 dark:text-gray-300">
            {new Date(state.lastUpdated).toLocaleString([], {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        </div>
        <div>
          <span className="text-xs text-gray-400">Hours Since Update</span>
          <p
            className={`font-medium ${
              state.hoursStale >= 24
                ? "text-red-600"
                : state.hoursStale >= 12
                ? "text-yellow-600"
                : "text-green-600"
            }`}
          >
            {state.hoursStale}h
            {state.hoursStale >= 24 && " ⚠ STALE"}
          </p>
        </div>
        {state.suggestedDelegate && (
          <div>
            <span className="text-xs text-gray-400">Suggested Delegate</span>
            <p className="text-gray-700 dark:text-gray-300">
              {state.suggestedDelegate}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
