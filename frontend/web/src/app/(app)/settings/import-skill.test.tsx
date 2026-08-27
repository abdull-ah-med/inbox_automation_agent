import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  ImportSkillDropzone,
  MAX_SKILL_ARCHIVE_BYTES,
  validateSkillArchiveClient,
} from "@/components/import-skill-dropzone"
import { SkillDuplicateCandidatesError } from "@/lib/api-client"
import type { ImportSkillResult } from "@/lib/types"

const successResult = (): ImportSkillResult => ({
  skill_id: "skill-1",
  name: "samplelab-rebilling",
  description: "Rebill SampleLab invoices",
  reference_files: ["references/client_rules.md", "references/output_format.md"],
  asset_files: [],
  warnings: ["scripts/ skipped (read-only constraint): scripts/run.py"],
  overwritten: false,
})

describe("validateSkillArchiveClient", () => {
  it("accepts .zip and .skill", () => {
    expect(
      validateSkillArchiveClient(new File(["pk"], "demo.zip", { type: "application/zip" })),
    ).toBeNull()
    expect(
      validateSkillArchiveClient(new File(["pk"], "demo.skill", { type: "application/zip" })),
    ).toBeNull()
  })

  it("rejects .txt client-side", () => {
    expect(
      validateSkillArchiveClient(new File(["hi"], "notes.txt", { type: "text/plain" })),
    ).toMatch(/zip or \.skill/i)
  })

  it("rejects archives over 10 MB client-side", () => {
    const big = new File([new Uint8Array(MAX_SKILL_ARCHIVE_BYTES + 1)], "big.zip", {
      type: "application/zip",
    })
    expect(validateSkillArchiveClient(big)).toMatch(/10 MB/i)
  })
})

describe("ImportSkillDropzone", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders warnings and reference file list on success", async () => {
    const user = userEvent.setup()
    const onImport = vi.fn().mockResolvedValue(successResult())

    render(<ImportSkillDropzone onImport={onImport} />)

    const input = screen.getByLabelText("Skill archive file input")
    const file = new File(["pk"], "samplelab-rebilling.zip", {
      type: "application/zip",
    })
    await user.upload(input, file)

    await waitFor(() => {
      expect(onImport).toHaveBeenCalledWith(file, undefined)
    })
    expect(await screen.findByText(/Imported samplelab-rebilling/)).toBeInTheDocument()
    expect(screen.getByText("references/client_rules.md")).toBeInTheDocument()
    expect(screen.getByText(/scripts\/ skipped/)).toBeInTheDocument()
  })

  it("surfaces server error text verbatim on failure", async () => {
    const user = userEvent.setup()
    const onImport = vi.fn().mockRejectedValue(new Error("Unsafe archive path: ../etc/passwd"))

    render(<ImportSkillDropzone onImport={onImport} />)

    const input = screen.getByLabelText("Skill archive file input")
    await user.upload(input, new File(["pk"], "bad.zip", { type: "application/zip" }))

    expect(await screen.findByText("Unsafe archive path: ../etc/passwd")).toBeInTheDocument()
  })

  it("rejects .txt on drop-zone without calling onImport", async () => {
    const user = userEvent.setup()
    const onImport = vi.fn()
    render(<ImportSkillDropzone onImport={onImport} />)

    const input = screen.getByLabelText("Skill archive file input")
    await user.upload(input, new File(["hello"], "notes.txt", { type: "text/plain" }))

    expect(await screen.findByText(/zip or \.skill/i)).toBeInTheDocument()
    expect(onImport).not.toHaveBeenCalled()
  })

  it("rejects oversized file without calling onImport", async () => {
    const user = userEvent.setup()
    const onImport = vi.fn()
    render(<ImportSkillDropzone onImport={onImport} />)

    const input = screen.getByLabelText("Skill archive file input")
    const big = new File([new Uint8Array(MAX_SKILL_ARCHIVE_BYTES + 1)], "big.zip", {
      type: "application/zip",
    })
    await user.upload(input, big)

    expect(await screen.findByRole("alert")).toHaveTextContent(/10 MB/i)
    expect(onImport).not.toHaveBeenCalled()
  })

  it("opens duplicate dialog and Overwrite retries with skill id", async () => {
    const user = userEvent.setup()
    const file = new File(["pk"], "demo.zip", { type: "application/zip" })
    const onImport = vi
      .fn()
      .mockRejectedValueOnce(
        new SkillDuplicateCandidatesError(
          [
            {
              id: "cand-1",
              name: "samplelab-rebilling",
              similarity: 0.91,
            },
          ],
          409,
          {
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
          },
        ),
      )
      .mockResolvedValueOnce({
        ...successResult(),
        overwritten: true,
      })

    render(<ImportSkillDropzone onImport={onImport} />)

    await user.upload(screen.getByLabelText("Skill archive file input"), file)

    expect(await screen.findByText("Similar skill found")).toBeInTheDocument()
    expect(screen.getByText("samplelab-rebilling")).toBeInTheDocument()
    expect(screen.getByText("91% similar")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Overwrite skill samplelab-rebilling/i }))

    await waitFor(() => {
      expect(onImport).toHaveBeenLastCalledWith(file, {
        overwrite: true,
        overwriteSkillId: "cand-1",
      })
    })
    expect(await screen.findByText(/Imported samplelab-rebilling/)).toBeInTheDocument()
  })

  it("Create as new retries with nameOverride", async () => {
    const user = userEvent.setup()
    const file = new File(["pk"], "demo.zip", { type: "application/zip" })
    const onImport = vi
      .fn()
      .mockRejectedValueOnce(
        new SkillDuplicateCandidatesError(
          [{ id: "cand-1", name: "samplelab-rebilling", similarity: 0.88 }],
          409,
        ),
      )
      .mockResolvedValueOnce({
        ...successResult(),
        name: "samplelab-rebilling-v2",
      })

    render(<ImportSkillDropzone onImport={onImport} />)
    await user.upload(screen.getByLabelText("Skill archive file input"), file)

    expect(await screen.findByText("Similar skill found")).toBeInTheDocument()
    await user.type(screen.getByLabelText("New skill name"), "samplelab-rebilling-v2")
    await user.click(screen.getByRole("button", { name: /Create skill with new name/i }))

    await waitFor(() => {
      expect(onImport).toHaveBeenLastCalledWith(file, {
        overwrite: false,
        nameOverride: "samplelab-rebilling-v2",
      })
    })
    expect(await screen.findByText(/Imported samplelab-rebilling-v2/)).toBeInTheDocument()
  })
})
