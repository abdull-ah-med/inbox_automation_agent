import { beforeEach, describe, expect, it, vi } from "vitest"

import { clearAuthSession, setAuthSession } from "@/features/auth/auth-store"
import { SkillDuplicateCandidatesError, api } from "@/lib/api-client"

describe("api.skills.import duplicate handling", () => {
  beforeEach(() => {
    clearAuthSession()
    vi.restoreAllMocks()
    setAuthSession({
      accessToken: "tok",
      expiresIn: 900,
      user: {
        id: "1",
        email: "elise@example.com",
        role: "admin",
        created_at: new Date().toISOString(),
      },
    })
  })

  it("throws SkillDuplicateCandidatesError on 409 duplicate_candidates", async () => {
    const payload = {
      detail: {
        code: "duplicate_candidates",
        candidates: [
          {
            id: "cand-1",
            name: "samplelab-rebilling",
            similarity: 0.91,
          },
        ],
      },
    }
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    const file = new File(["pk"], "demo.zip", { type: "application/zip" })
    const caught = await api.skills.import(file).then(
      () => {
        throw new Error("expected SkillDuplicateCandidatesError")
      },
      (error: unknown) => error,
    )

    expect(caught).toBeInstanceOf(SkillDuplicateCandidatesError)
    expect(caught).toMatchObject({
      candidates: [
        {
          id: "cand-1",
          name: "samplelab-rebilling",
          similarity: 0.91,
        },
      ],
    })
  })

  it("sends overwrite FormData fields on retry options", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          skill_id: "skill-1",
          name: "demo-skill",
          description: "d",
          reference_files: [],
          asset_files: [],
          warnings: [],
          overwritten: true,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    const file = new File(["pk"], "demo.zip", { type: "application/zip" })
    await api.skills.import(file, {
      overwrite: true,
      overwriteSkillId: "cand-1",
      nameOverride: "demo-skill-v2",
      category: "billing",
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    const form = init.body as FormData
    expect(form.get("overwrite")).toBe("true")
    expect(form.get("overwrite_skill_id")).toBe("cand-1")
    expect(form.get("name_override")).toBe("demo-skill-v2")
    expect(form.get("category")).toBe("billing")
    expect(form.get("file")).toBeInstanceOf(File)
  })
})
