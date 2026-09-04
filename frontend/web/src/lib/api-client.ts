/**
 * Barrel for the FastAPI client. Domain modules live in ./api/.
 */

export {
  ApiError,
  apiFetch,
  apiFetchMultipart,
  SkillDuplicateCandidatesError,
} from "@/lib/api/client"
export type { ImportSkillOptions, SkillDuplicateCandidate } from "@/lib/api/client"

import { authApi } from "@/lib/api/auth"
import { chatApi } from "@/lib/api/chat"
import {
  dashboardApi,
  draftsApi,
  rejectionMemoryApi,
  replyMemoryApi,
  reportsApi,
  searchApi,
  toneProfilesApi,
} from "@/lib/api/dashboard"
import {
  feedbackAtomsApi,
  promotionProposalsApi,
  teachingNotesApi,
  urgencyRulesApi,
} from "@/lib/api/feedback-loops"
import { mailboxContactsApi } from "@/lib/api/mailbox-contacts"
import { mailboxesApi } from "@/lib/api/mailboxes"
import { skillCandidatesApi, skillsApi } from "@/lib/api/skills"
import { threadsApi } from "@/lib/api/threads"

export const api = {
  ...authApi,
  dashboard: dashboardApi,
  reports: reportsApi,
  search: searchApi,
  chat: chatApi,
  mailboxes: mailboxesApi,
  mailboxContacts: mailboxContactsApi,
  threads: threadsApi,
  drafts: draftsApi,
  skills: skillsApi,
  replyMemory: replyMemoryApi,
  rejectionMemory: rejectionMemoryApi,
  toneProfiles: toneProfilesApi,
  skillCandidates: skillCandidatesApi,
  teachingNotes: teachingNotesApi,
  feedbackAtoms: feedbackAtomsApi,
  promotionProposals: promotionProposalsApi,
  urgencyRules: urgencyRulesApi,
}
