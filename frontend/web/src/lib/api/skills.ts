import { apiFetch, apiFetchBytes, apiFetchMultipart } from "@/lib/api/client"
import type {
  ImportSkillOptions,
  ImportSkillResult,
  SkillCandidateResponse,
  SkillCreate,
  SkillFileMeta,
  SkillResponse,
  SkillUpdate,
} from "@/lib/types"

export const skillsApi = {
  list() {
    return apiFetch<SkillResponse[]>("/api/skills")
  },
  create(body: SkillCreate) {
    return apiFetch<SkillResponse>("/api/skills", {
      method: "POST",
      body: JSON.stringify(body),
    })
  },
  update(id: string, body: SkillUpdate) {
    return apiFetch<SkillResponse>(`/api/skills/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    })
  },
  delete(id: string) {
    return apiFetch<void>(`/api/skills/${id}`, {
      method: "DELETE",
    })
  },
  import(file: File, options?: ImportSkillOptions) {
    const form = new FormData()
    form.append("file", file)
    if (options?.overwrite != null) {
      form.append("overwrite", String(options.overwrite))
    }
    if (options?.overwriteSkillId) {
      form.append("overwrite_skill_id", options.overwriteSkillId)
    }
    if (options?.nameOverride) {
      form.append("name_override", options.nameOverride)
    }
    if (options?.category) {
      form.append("category", options.category)
    }
    return apiFetchMultipart<ImportSkillResult>("/api/skills/import", form)
  },
  listFiles(id: string) {
    return apiFetch<SkillFileMeta[]>(`/api/skills/${id}/files`)
  },
  async getFile(id: string, relativePath: string) {
    const encoded = relativePath
      .split("/")
      .map((segment) => encodeURIComponent(segment))
      .join("/")
    return apiFetchBytes(`/api/skills/${id}/files/${encoded}`)
  },
}

export const skillCandidatesApi = {
  list(mailbox?: string) {
    const qs = new URLSearchParams()
    if (mailbox) qs.set("mailbox", mailbox)
    const query = qs.toString()
    return apiFetch<SkillCandidateResponse[]>(`/api/skill-candidates${query ? `?${query}` : ""}`)
  },
  accept(id: string) {
    return apiFetch<SkillResponse>(`/api/skill-candidates/${id}/accept`, {
      method: "POST",
    })
  },
  dismiss(id: string) {
    return apiFetch<SkillCandidateResponse>(`/api/skill-candidates/${id}/dismiss`, {
      method: "POST",
    })
  },
}
