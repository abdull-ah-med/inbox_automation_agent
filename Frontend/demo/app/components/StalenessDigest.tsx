"use client";

interface StaleThread {
  subject: string;
  inbox: string;
  hoursStale: number;
  lastStatus: string;
  suggestedDelegate: string;
}

const staleThreads: StaleThread[] = [
  {
    subject: "RE: Order #PSC-39104 — Missing consent form",
    inbox: "Client Relations",
    hoursStale: 36,
    lastStatus: "Awaiting Client",
    suggestedDelegate: "Follow up with client directly",
  },
  {
    subject: "Background check discrepancy — Williams, R.",
    inbox: "Client Relations",
    hoursStale: 28,
    lastStatus: "Awaiting Vendor",
    suggestedDelegate: "Escalate to vendor liaison",
  },
  {
    subject: "RE: Partnership agreement renewal — Q3",
    inbox: "Intermediary",
    hoursStale: 48,
    lastStatus: "Awaiting Partner Services",
    suggestedDelegate: "Kelvin to review terms",
  },
];

export default function StalenessDigest() {
  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-5">
      <h3 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">
        24h Staleness Digest
      </h3>
      <p className="text-xs text-gray-400 mb-4">
        Threads untouched for more than 24 hours
      </p>

      <div className="space-y-3">
        {staleThreads.map((thread, i) => (
          <div
            key={i}
            className="flex items-start gap-3 p-3 bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-900/40 rounded-lg"
          >
            <span className="text-lg mt-0.5">⚠</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                {thread.subject}
              </p>
              <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1 text-xs text-gray-500 dark:text-gray-400">
                <span>{thread.inbox}</span>
                <span className="text-red-600 dark:text-red-400 font-medium">
                  {thread.hoursStale}h stale
                </span>
                <span>Status: {thread.lastStatus}</span>
              </div>
              <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">
                → {thread.suggestedDelegate}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
