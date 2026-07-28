/** API types mirroring backend dashboard schemas. */

export interface UserMe {
  id: string
  email: string
  role: string
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: "Bearer"
  expires_in: number
  user: UserMe
}

export interface TriageFlags {
  is_spam: boolean | null
  has_action_items: boolean | null
  needs_context: boolean | null
  spam_reason: string | null
  context_reason: string | null
  action_items_summary: string | null
  outcome: string | null
}

export interface ThreadSummary {
  id: string
  mailbox: string
  mailbox_key: string
  subject: string
  state: string
  urgency: string | null
  category: string | null
  last_message_at: string | null
  last_sender: string | null
  preview: string | null
  staleness_hours: number
  message_count: number
  has_draft: boolean
  teaching_note: string | null
  triage: TriageFlags | null
  outlook_url: string | null
}

export interface MailboxOverview {
  mailbox: string
  email_address: string
  label: string
  thread_count: number
  unread_count: number
  awaiting_action_count: number
  filtered_count: number
  stale_count: number
  urgency_breakdown: Record<string, number>
  recent_threads: ThreadSummary[]
}

export interface AuditEntry {
  timestamp: string
  event: string
  detail: string
  source: string
}

export interface DashboardOverview {
  mailboxes: MailboxOverview[]
  total_threads: number
  total_awaiting: number
  total_stale: number
  needs_attention: ThreadSummary[]
  recent_activity: AuditEntry[]
  updated_at: string
}

export interface ThreadList {
  items: ThreadSummary[]
  next_cursor: string | null
}

export interface MessageDetail {
  id: string
  direction: string
  sender: string
  to: string[]
  cc: string[]
  body_text: string
  body_preview: string | null
  received_at: string
  has_attachments: boolean
  outlook_url: string | null
}

export interface ClassificationView {
  category: string
  intent: string
  urgency: string
  confidence: number | null
  entities: Record<string, unknown>
  model_version: string
  created_at: string
}

export interface DraftView {
  id: string
  subject: string
  body: string
  teaching_note: string
  urgency: string | null
  urgency_reason: string | null
  forward_to: string | null
  created_at: string
}

export interface ThreadDetail {
  thread: ThreadSummary
  messages: MessageDetail[]
  classification: ClassificationView | null
  draft: DraftView | null
  triage: TriageFlags | null
  audit_log: AuditEntry[]
}
