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
  replyMemoryApi,
  reportsApi,
  searchApi,
  toneProfilesApi,
} from "@/lib/api/dashboard"
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
  threads: threadsApi,
  drafts: draftsApi,
  skills: skillsApi,
  replyMemory: replyMemoryApi,
  toneProfiles: toneProfilesApi,
  skillCandidates: skillCandidatesApi,
}
