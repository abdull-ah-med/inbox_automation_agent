"use client";

import { useState } from "react";
import { sampleResults } from "./data/sampleEmails";
import EmailCard from "./components/EmailCard";
import ClassificationPanel from "./components/ClassificationPanel";
import SlackPreview from "./components/SlackPreview";
import AuditLog from "./components/AuditLog";
import ThreadStatePanel from "./components/ThreadStatePanel";
import StalenessDigest from "./components/StalenessDigest";

const tabs = [
  { id: "classification", label: "Classification" },
  { id: "slack", label: "Slack Queue" },
  { id: "thread", label: "Thread State" },
  { id: "audit", label: "Audit Log" },
  { id: "staleness", label: "Staleness Digest" },
] as const;

type TabId = (typeof tabs)[number]["id"];

export default function Home() {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [activeTab, setActiveTab] = useState<TabId>("classification");
  const [processing, setProcessing] = useState(false);
  const [processed, setProcessed] = useState(false);

  const result = sampleResults[selectedIndex];

  const handleProcess = () => {
    setProcessing(true);
    setProcessed(false);
    // Simulate processing delay
    setTimeout(() => {
      setProcessing(false);
      setProcessed(true);
    }, 1500);
  };

  const handleSelectEmail = (index: number) => {
    setSelectedIndex(index);
    setProcessed(false);
    setProcessing(false);
    setActiveTab("classification");
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      {/* Header */}
      <header className="bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              Inbox Triage Automation
            </h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Demo — Read-only assistant for inbox classification, drafting, and review
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400 px-2 py-1 rounded font-medium">
              DEMO MODE
            </span>
            <span className="text-xs bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 px-2 py-1 rounded">
              Read-Only
            </span>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto p-6">
        <div className="flex gap-6">
          {/* Left sidebar — Email list */}
          <div className="w-80 shrink-0">
            <div className="sticky top-6">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
                  Inbox ({sampleResults.length} emails)
                </h2>
                <span className="text-xs text-gray-400">Sample data</span>
              </div>
              <div className="space-y-2">
                {sampleResults.map((r, i) => (
                  <EmailCard
                    key={r.email.id}
                    email={r.email}
                    selected={selectedIndex === i}
                    onClick={() => handleSelectEmail(i)}
                  />
                ))}
              </div>
            </div>
          </div>

          {/* Main content */}
          <div className="flex-1 min-w-0">
            {/* Email preview */}
            <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg p-5 mb-4">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                    {result.email.subject}
                  </h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                    From: {result.email.from}
                  </p>
                  <p className="text-sm text-gray-500 dark:text-gray-400">
                    To: {result.email.to}
                  </p>
                  <p className="text-xs text-gray-400 mt-1">
                    {new Date(result.email.receivedAt).toLocaleString()}
                  </p>
                </div>
              </div>

              <div className="border-t border-gray-100 dark:border-gray-800 pt-3">
                <pre className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-sans leading-relaxed">
                  {result.email.body}
                </pre>
              </div>
            </div>

            {/* Process button */}
            {!processed && (
              <div className="flex justify-center mb-4">
                <button
                  onClick={handleProcess}
                  disabled={processing}
                  className={`px-6 py-2.5 rounded-lg font-medium text-sm transition-all cursor-pointer ${
                    processing
                      ? "bg-gray-200 dark:bg-gray-700 text-gray-400 cursor-not-allowed"
                      : "bg-blue-600 text-white hover:bg-blue-700 active:bg-blue-800"
                  }`}
                >
                  {processing ? (
                    <span className="flex items-center gap-2">
                      <svg
                        className="animate-spin h-4 w-4"
                        viewBox="0 0 24 24"
                        fill="none"
                      >
                        <circle
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                          className="opacity-25"
                        />
                        <path
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                          className="opacity-75"
                        />
                      </svg>
                      Processing email…
                    </span>
                  ) : (
                    "▶ Run Triage Agent"
                  )}
                </button>
              </div>
            )}

            {/* Results */}
            {processed && (
              <div>
                {/* Tab navigation */}
                <div className="flex gap-1 mb-4 border-b border-gray-200 dark:border-gray-700">
                  {tabs.map((tab) => (
                    <button
                      key={tab.id}
                      onClick={() => setActiveTab(tab.id)}
                      className={`px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${
                        activeTab === tab.id
                          ? "text-blue-600 dark:text-blue-400 border-b-2 border-blue-600 dark:border-blue-400"
                          : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {/* Tab content */}
                <div>
                  {activeTab === "classification" && (
                    <ClassificationPanel
                      classification={result.classification}
                    />
                  )}
                  {activeTab === "slack" && <SlackPreview result={result} />}
                  {activeTab === "thread" && (
                    <ThreadStatePanel state={result.threadState} />
                  )}
                  {activeTab === "audit" && (
                    <AuditLog entries={result.auditLog} />
                  )}
                  {activeTab === "staleness" && <StalenessDigest />}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
