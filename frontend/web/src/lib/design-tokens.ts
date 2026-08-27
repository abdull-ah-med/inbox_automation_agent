/** Inbox accent palette from the demo + helpers. */

import type { CSSProperties } from "react"

const DEMO_COLORS = {
  "client-relations": "#2563eb",
  sales: "#16a34a",
  vendor: "#d97706",
  intermediary: "#7c3aed",
} as const

/** Map real configured mailboxes onto the demo palette in a stable order. */
const KEY_COLOR_ORDER = [
  DEMO_COLORS["client-relations"],
  DEMO_COLORS.sales,
  DEMO_COLORS.vendor,
  DEMO_COLORS.intermediary,
] as const

const KNOWN_COLORS: Record<string, string> = {
  ...DEMO_COLORS,
  inquiries: DEMO_COLORS["client-relations"],
  support: DEMO_COLORS.sales,
  info: DEMO_COLORS.vendor,
  sampleagent: DEMO_COLORS.intermediary,
}

const KNOWN_LABELS: Record<string, string> = {
  "client-relations": "Client Relations",
  sales: "Sales",
  vendor: "Vendor",
  intermediary: "Intermediary",
  sampleagent: "Elise",
}

function hashKey(key: string): number {
  let h = 0
  for (let i = 0; i < key.length; i += 1) {
    h = (h * 31 + key.charCodeAt(i)) >>> 0
  }
  return h
}

export function inboxColor(key: string): string {
  if (KNOWN_COLORS[key]) return KNOWN_COLORS[key]
  return KEY_COLOR_ORDER[hashKey(key) % KEY_COLOR_ORDER.length]
}

/**
 * Light: tinted chip + accent text (readable on white).
 * Dark: solid accent fill + white text (previous look).
 */
export const inboxChipClassName =
  "rounded-full px-2 py-0.5 text-xs font-medium text-[color:var(--inbox-accent)] bg-[color-mix(in_srgb,var(--inbox-accent)_15%,white)] dark:bg-[var(--inbox-accent)] dark:text-white"

export function inboxAccentStyle(key: string): CSSProperties {
  return { ["--inbox-accent" as string]: inboxColor(key) }
}

export function inboxLabel(key: string, fallback?: string): string {
  if (KNOWN_LABELS[key]) return KNOWN_LABELS[key]
  if (fallback?.trim()) return fallback
  return key
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

export { formatRelativeTime } from "@/lib/dates"

export function formatEventName(event: string): string {
  return event.replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}
