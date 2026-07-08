"use client";

import { Email } from "../data/sampleEmails";

const inboxColors: Record<string, string> = {
  "client-relations": "#2563eb",
  sales: "#16a34a",
  vendor: "#d97706",
  intermediary: "#7c3aed",
};

const inboxLabels: Record<string, string> = {
  "client-relations": "Client Relations",
  sales: "Sales",
  vendor: "Vendor",
  intermediary: "Intermediary",
};

export default function EmailCard({
  email,
  selected,
  onClick,
}: {
  email: Email;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left p-4 border rounded-lg transition-all cursor-pointer ${
        selected
          ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30"
          : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <span
          className="text-xs font-medium px-2 py-0.5 rounded-full text-white shrink-0"
          style={{ backgroundColor: inboxColors[email.inbox] }}
        >
          {inboxLabels[email.inbox]}
        </span>
        {!email.isRead && (
          <span className="w-2 h-2 rounded-full bg-blue-500 shrink-0 mt-1" />
        )}
      </div>
      <p className="font-medium text-sm mt-2 text-gray-900 dark:text-gray-100 truncate">
        {email.subject}
      </p>
      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
        {email.from}
      </p>
      <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
        {new Date(email.receivedAt).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </p>
    </button>
  );
}
