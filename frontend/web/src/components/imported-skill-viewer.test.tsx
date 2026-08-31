import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const getFile = vi.fn()
const listFiles = vi.fn()
const revokeObjectURL = vi.fn()
const createObjectURL = vi.fn(() => "blob:skill-file")

vi.mock("@/lib/api-client", () => ({
  api: {
    skills: {
      listFiles: (...args: unknown[]) => listFiles(...args),
      getFile: (...args: unknown[]) => getFile(...args),
    },
  },
}))

import { ImportedSkillViewer } from "@/components/imported-skill-viewer"
import type { SkillResponse } from "@/lib/types"

const skill: SkillResponse = {
  id: "skill-1",
  name: "Packet helper",
  description: "Helps with packets",
  content: "# Skill",
  category: null,
  always_apply: false,
  is_active: true,
  source_kind: "imported",
  imported_zip_sha256: null,
  raw_frontmatter: null,
  reference_file_count: 0,
  asset_file_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

const renderViewer = (open = true) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ImportedSkillViewer skill={skill} open={open} onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  )
}

describe("ImportedSkillViewer blob URL cleanup", () => {
  beforeEach(() => {
    getFile.mockReset()
    listFiles.mockReset()
    revokeObjectURL.mockReset()
    createObjectURL.mockClear()
    listFiles.mockResolvedValue([
      {
        id: "file-1",
        relative_path: "assets/guide.pdf",
        mime_type: "application/pdf",
        size_bytes: 2048,
        kind: "asset",
      },
    ])
    getFile.mockResolvedValue({
      blob: new Blob(["%PDF"], { type: "application/pdf" }),
    })
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("revokes the blob URL when the file row is closed", async () => {
    // Bug this catches: createObjectURL without revokeObjectURL on close/unmount.
    const user = userEvent.setup()
    renderViewer()
    await user.click(await screen.findByRole("button", { name: /toggle file assets\/guide\.pdf/i }))
    expect(
      await screen.findByRole("link", { name: /download assets\/guide\.pdf/i }),
    ).toHaveAttribute("href", "blob:skill-file")
    expect(createObjectURL).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole("button", { name: /toggle file assets\/guide\.pdf/i }))
    await waitFor(() => {
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:skill-file")
    })
  })
})
