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
  draft_needed?: boolean | null
  needs_context: boolean | null
  spam_reason: string | null
  context_reason: string | null
  action_items_summary: string | null
  routing_category?: string | null
  is_internal?: boolean | null
  is_automated?: boolean | null
  outcome: string | null
}

export interface BadgeNow {
  kind: string
  label: string
}

export type TriageHistory = Pick<TriageFlags, "has_action_items" | "needs_context" | "is_spam"> & {
  action_items_summary?: string | null
  context_reason?: string | null
  spam_reason?: string | null
}

export interface ThreadPresentation {
  is_finished: boolean
  open_work: boolean
  in_needs_attention: boolean
  urgency_assessed: string | null
  urgency_active: boolean
  badges_now: BadgeNow[]
  triage_history: TriageHistory
  suggest_resolve_default: boolean
  show_resolution_banner: boolean
  resolution_mode?: "auto" | "manual" | null
  disposition?: string | null
  primary_badge?: BadgeNow | null
  resolution_reason?: string | null
  resolution_summary?: string | null
}

export interface ActivityEntry {
  title: string
  body: string
  actor_kind: string
  event_type: string
  timestamp: string
}

export interface ThreadSummary {
  id: string
  mailbox: string
  mailbox_key: string
  subject: string
  state: string
  urgency: string | null
  urgency_reason: string | null
  category: string | null
  last_message_at: string | null
  last_sender: string | null
  preview: string | null
  staleness_hours: number
  message_count: number
  has_draft: boolean
  has_letter?: boolean
  teaching_note: string | null
  triage: TriageFlags | null
  outlook_url: string | null
  presentation?: ThreadPresentation | null
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
  open_fyi_count?: number
  recently_resolved_draftassistant_count?: number
  urgency_breakdown: Record<string, number>
  recent_threads: ThreadSummary[]
}

export interface AuditEntry {
  timestamp: string
  event: string
  detail: string
  source: string
}

export type NeedsAttentionSort = "urgency" | "recent"

export interface DashboardOverview {
  mailboxes: MailboxOverview[]
  total_threads: number
  total_awaiting: number
  total_stale: number
  needs_attention: ThreadSummary[]
  open_fyi?: ThreadSummary[]
  recently_resolved_by_draftassistant?: ThreadSummary[]
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
  bcc: string[]
  body_text: string
  reply_text: string
  body_preview: string | null
  received_at: string
  has_attachments: boolean
  outlook_url: string | null
  meeting_message_type?: string | null
  meeting_response_type?: string | null
  sender_name?: string | null
  sender_salute_name?: string | null
  summary_one_line?: string | null
  summary_ask?: string | null
  summary_intent?: string | null
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

export interface SuggestedAction {
  step: number
  action: string
  stakeholder: string | null
  rationale: string
}

export interface AppliedSkill {
  id: string
  name: string
}

export interface DraftToolCall {
  skill_id: string
  path: string
  is_error: boolean
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
  suggested_actions: SuggestedAction[]
  approved_at: string | null
  rejected_at: string | null
  edited_body: string | null
  feedback_note: string | null
  feedback_action: string | null
  feedback_reason_code: string | null
  routing_category: string | null
  approval_note: string | null
  approval_scope: "once" | "similar" | "sender_address" | "mailbox" | null
  applied_skills: AppliedSkill[]
  tool_calls: DraftToolCall[] | null
  correct_actions: SuggestedAction[]
}

export type SkillSourceKind = "inline" | "imported"
export type SkillFileKind = "reference" | "asset"

export interface SkillFileMeta {
  id: string
  relative_path: string
  kind: SkillFileKind
  mime_type: string
  size_bytes: number
  created_at: string
}

export interface SkillResponse {
  id: string
  name: string
  description: string | null
  content: string
  category: string | null
  always_apply: boolean
  is_active: boolean
  source_kind: SkillSourceKind
  imported_zip_sha256: string | null
  raw_frontmatter: Record<string, unknown> | null
  reference_file_count: number
  asset_file_count: number
  created_at: string
  updated_at: string
}

export interface ImportSkillResult {
  skill_id: string
  name: string
  description: string
  reference_files: string[]
  asset_files: string[]
  warnings: string[]
  overwritten: boolean
}

export interface ImportSkillOptions {
  overwrite?: boolean
  overwriteSkillId?: string
  nameOverride?: string
  category?: string
}

export interface SkillDuplicateCandidate {
  id: string
  name: string
  similarity: number
}

export interface SkillCreate {
  name: string
  description?: string
  content: string
  category?: string | null
  always_apply?: boolean
  is_active?: boolean
}

export interface SkillUpdate {
  name?: string
  description?: string
  content?: string
  category?: string | null
  always_apply?: boolean
  is_active?: boolean
}

export interface ReplyMemoryResponse {
  id: string
  draft_id: string
  thread_id?: string | null
  mailbox: string
  reply_text: string
  preview_line?: string | null
  draft_subject?: string | null
  sender_email?: string | null
  receiver_email?: string | null
  reason_code?: string | null
  reason_text?: string | null
  original_email_preview: string | null
  learning_note?: string | null
  is_excluded: boolean
  created_at: string
}

export interface RejectionMemoryResponse {
  id: string
  draft_id: string
  thread_id?: string | null
  mailbox: string
  routing_category: string
  reason_code: string
  note: string
  reason_text?: string | null
  is_excluded: boolean
  created_at: string | null
  draft_subject: string | null
  draft_body?: string | null
  draft_body_preview: string | null
  preview_line?: string | null
  sender_email?: string | null
  receiver_email?: string | null
}

export interface ToneProfileData {
  formality: "formal" | "professional" | "conversational"
  greeting_pattern: string | null
  sign_off_pattern: string | null
  typical_length: "short" | "medium" | "long"
  favored_phrases: string[]
  avoided_phrases: string[]
  behavioral_rules: string[]
}

export interface ToneProfileResponse {
  id: string
  mailbox: string
  routing_category: string
  profile: ToneProfileData
  sample_count: number
  version: number
  built_at: string
}

export interface SkillCandidateResponse {
  id: string
  mailbox: string
  routing_category: string
  reason_code: string
  proposed_name: string
  proposed_content: string
  source_rejection_ids: string[]
  status: "pending" | "accepted" | "dismissed"
  created_at: string
}

export interface RelatedThreadItem {
  thread_id: string
  mailbox: string
  subject: string
  sender: string
  last_message_at: string | null
  urgency: string | null
  score: number
  status: "proposed" | "confirmed" | "dismissed"
  match_reasons?: string[]
}

export interface RelatedThreadList {
  items: RelatedThreadItem[]
}

export interface ReplyAddresseeView {
  email: string
  salute_name: string
  source: string
  source_kind: string
  directory_hit: boolean
}

export interface MailboxContactView {
  email: string
  full_name: string
  first_name: string
  notes: string | null
  created_at: string
  updated_at: string
}

export interface MailboxContactListResponse {
  items: MailboxContactView[]
  total: number
}

export interface MailboxContactUpsert {
  email: string
  full_name?: string
  first_name: string
  notes?: string | null
}

export interface MailboxContactPatch {
  email: string
  first_name?: string | null
  full_name?: string | null
  notes?: string | null
}

export interface ThreadContextFact {
  id: string
  body: string
  source_message_id: string | null
  created_at: string | null
  source_received_at?: string | null
}

export interface ThreadContextView {
  version: number
  user_notes: string
  facts: ThreadContextFact[]
  updated_at: string | null
  rebuild_in_progress?: boolean
  rebuild_error?: string | null
  needs_initial_extract?: boolean
}

export interface ThreadDetail {
  thread: ThreadSummary
  messages: MessageDetail[]
  classification: ClassificationView | null
  draft: DraftView | null
  triage: TriageFlags | null
  audit_log: AuditEntry[]
  activity?: ActivityEntry[]
  sent_reply?: SentReplyView | null
  draft_vs_sent_diff?: DraftVsSentDiff | null
  associated_threads?: RelatedThreadItem[]
  reply_addressee?: ReplyAddresseeView | null
  draft_regen_in_progress?: boolean
  draft_regen_error?: string | null
}

export interface DraftRegenAccepted {
  status: "running"
  thread_id: string
  draft_regen_in_progress: boolean
  draft_regen_error?: string | null
}

export interface MarkNotSpamResponse {
  thread_id: string
  state: string
  is_spam: boolean
  sender_address: string
  outlook_unchanged: boolean
}

export interface SentReplyView {
  id: string
  thread_id: string
  message_id: string
  draft_id: string | null
  sent_body_snapshot: string
  sent_at: string
  matched_by: "approved_draft" | "time_window" | "manual"
  created_at?: string | null
}

export interface DraftVsSentDiff {
  added: string[]
  removed: string[]
}

export interface ChatCitation {
  thread_id: string
  mailbox: string
  subject: string | null
  state: string
  urgency: string | null
  snippet?: string | null
  url_path: string
}

export interface ChatCitedThread {
  thread_id: string
  subject?: string | null
}

export interface ChatHistoryTurn {
  role: "user" | "assistant"
  content: string
  citations?: ChatCitedThread[]
}

export interface ChatAskRequest {
  message: string
  mailbox?: string
  limit?: number
  history?: ChatHistoryTurn[]
  bypass_cache?: boolean
  session_id?: string
}

export interface ChatSessionCreateResponse {
  session_id: string
  mailbox: string | null
}

export type ChatSessionMessage = ChatHistoryTurn

export interface ChatSessionResponse {
  session_id: string
  mailbox: string | null
  messages: ChatSessionMessage[]
  summary: string
}

export interface SearchHit {
  thread_id: string
  mailbox: string
  conversation_id: string
  subject: string | null
  state: string
  urgency: string | null
  snippet: string
  score: number
  last_message_at: string | null
}

export interface SearchResponse {
  query: string
  mailbox: string | null
  hits: SearchHit[]
}

export type TeachingNoteCreateScope =
  | "sender_address"
  | "sender_domain"
  | "mailbox+routing_category"
  | "mailbox"

export type TeachingNoteStatus = "active" | "paused" | "archived"

export interface TeachingNote {
  id: string
  mailbox: string
  title: string
  body: string
  applies_when: string | null
  scope: string
  scope_key: string
  status: TeachingNoteStatus
  origin: string
  origin_atom_id: string | null
  person_bound: boolean
  hit_count: number
  precision_num: number
  precision_den: number
  created_at: string | null
  updated_at: string | null
  proposal_id?: string | null
}

export type FeedbackAtomRole = "Fix" | "Spec" | "Null"

export interface FeedbackAtom {
  id: string
  source_kind: string
  source_id: string
  mailbox: string
  atom_text: string
  role: FeedbackAtomRole
  applies_when: string | null
  scope: string
  scope_key: string
  is_active: boolean
  hit_count: number
  precision_num: number
  precision_den: number
  expires_at: string | null
  person_bound: boolean
  promoted_from_atom_id: string | null
  created_at: string | null
}

export type PromotionProposalKind = "atom_widening" | "urgency_rule" | "note_widening"
export type PromotionProposalStatus = "pending" | "accepted" | "dismissed" | "expired" | "reverted"

export interface PromotionProposal {
  id: string
  mailbox: string
  kind: PromotionProposalKind
  payload: Record<string, unknown>
  impact_num: number
  impact_den: number
  precision_num: number | null
  precision_den: number | null
  evidence_ids: string[]
  status: PromotionProposalStatus
  expires_at: string
  created_at: string | null
  dedupe_key?: string | null
}

export type UrgencyRuleStatus = "canary" | "active" | "paused" | "archived"

export interface UrgencyRule {
  id: string
  mailbox: string
  scope: string
  scope_key: string
  condition: Record<string, unknown>
  action: Record<string, unknown>
  status: UrgencyRuleStatus
  canary_until: string | null
  activated_at: string | null
  paused_at: string | null
  impact_num: number | null
  impact_den: number | null
  precision_num: number | null
  precision_den: number | null
  hit_count: number
  override_count: number
  person_bound: boolean
  previous_status: string | null
  created_at: string | null
  updated_at: string | null
}
