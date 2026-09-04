import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ContextSection } from "@/components/thread-triage/context-section"
import { ApiError } from "@/lib/api/client"
import type { ThreadContextView } from "@/lib/types"
import { renderWithProviders } from "@/test/render"

const getContextMock = vi.fn()
const saveUserNotesMock = vi.fn()
const rebuildContextMock = vi.fn()

const discardContextFactMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      getContext: (...args: unknown[]) => getContextMock(...args),
      saveUserNotes: (...args: unknown[]) => saveUserNotesMock(...args),
      rebuildContext: (...args: unknown[]) => rebuildContextMock(...args),
      discardContextFact: (...args: unknown[]) => discardContextFactMock(...args),
    },
  },
}))

const context: ThreadContextView = {
  version: 3,
  user_notes: "Do not CC legal",
  facts: [
    {
      id: "fact-1",
      body: "Client asked for a Friday callback",
      source_message_id: "msg-a",
      created_at: "2026-09-04T12:00:00Z",
      source_received_at: "2026-08-27T14:14:00Z",
    },
  ],
  updated_at: "2026-09-04T12:00:00Z",
}

describe("ContextSection", () => {
  beforeEach(() => {
    getContextMock.mockReset()
    saveUserNotesMock.mockReset()
    rebuildContextMock.mockReset()
    discardContextFactMock.mockReset()
    getContextMock.mockResolvedValue(context)
    saveUserNotesMock.mockResolvedValue({ ...context, version: 4 })
    rebuildContextMock.mockResolvedValue(context)
    discardContextFactMock.mockResolvedValue({ ...context, facts: [] })
  })

  it("saves user_notes and expected_version literals", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ContextSection threadId="thread-1" />)

    const notes = await screen.findByLabelText("Thread context user notes")
    expect(notes).toHaveValue("Do not CC legal")

    await user.clear(notes)
    await user.type(notes, "Keep billing on the thread")
    await user.click(screen.getByRole("button", { name: "Save notes" }))

    await waitFor(() => {
      expect(saveUserNotesMock).toHaveBeenCalledWith("thread-1", {
        user_notes: "Keep billing on the thread",
        expected_version: 3,
      })
    })
  })

  it("shows reload copy after a 409 conflict", async () => {
    const user = userEvent.setup()
    saveUserNotesMock.mockRejectedValue(new ApiError("Conflict", 409))
    renderWithProviders(<ContextSection threadId="thread-1" />)

    const notes = await screen.findByLabelText("Thread context user notes")
    await user.type(notes, " extra")
    await user.click(screen.getByRole("button", { name: "Save notes" }))

    expect(
      await screen.findByText("Someone else saved these notes. Reload and try again."),
    ).toBeInTheDocument()
    expect(getContextMock).toHaveBeenCalledTimes(2)
  })

  it("disables Rebuild while notes are dirty", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ContextSection threadId="thread-1" />)

    const rebuild = await screen.findByRole("button", { name: "Rebuild facts" })
    expect(rebuild).toBeEnabled()

    await user.type(screen.getByLabelText("Thread context user notes"), " extra")
    expect(screen.getByRole("button", { name: "Rebuild facts" })).toBeDisabled()
  })

  it("lists fact bodies without treating the teaching note as the thread story", async () => {
    renderWithProviders(<ContextSection threadId="thread-1" />)

    expect(await screen.findByText("Client asked for a Friday callback")).toBeInTheDocument()
    expect(screen.queryByText("Acknowledge and resolve.")).toBeNull()
  })

  it("stamps each fact with the source email date, not extract time", async () => {
    renderWithProviders(<ContextSection threadId="thread-1" />)

    const stamp = await screen.findByText(/8\/27\/2026/)
    expect(stamp.tagName).toBe("TIME")
    expect(stamp).toHaveAttribute("dateTime", "2026-08-27T14:14:00Z")
    expect(screen.queryByText(/Extracted/)).toBeNull()
  })

  it("discards a fact by id", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ContextSection threadId="thread-1" />)

    await user.click(
      await screen.findByRole("button", {
        name: "Discard fact Client asked for a Friday callback",
      }),
    )

    await waitFor(() => {
      expect(discardContextFactMock).toHaveBeenCalledWith("thread-1", "fact-1")
    })
    expect(
      await screen.findByText("No useful facts yet. Rebuild reads the whole thread."),
    ).toBeInTheDocument()
  })

  it("keeps Rebuild pending copy until the whole-thread extract returns", async () => {
    rebuildContextMock.mockResolvedValue({ ...context, rebuild_in_progress: true })
    const user = userEvent.setup()
    renderWithProviders(<ContextSection threadId="thread-1" />)

    await user.click(await screen.findByRole("button", { name: "Rebuild facts" }))

    expect(
      await screen.findByText("Reading the whole thread and extracting facts."),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Rebuild facts" })).toBeDisabled()
  })

  it("shows in-progress copy after refresh when the server is still extracting", async () => {
    getContextMock.mockResolvedValue({ ...context, rebuild_in_progress: true })
    renderWithProviders(<ContextSection threadId="thread-1" />)

    expect(
      await screen.findByText("Reading the whole thread and extracting facts."),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Rebuild facts" })).toBeDisabled()
  })

  it("shows the rate-limit message instead of a generic rebuild failure", async () => {
    rebuildContextMock.mockRejectedValue(
      new ApiError("You have tried too many times. Please try again after some time.", 429),
    )
    const user = userEvent.setup()
    renderWithProviders(<ContextSection threadId="thread-1" />)

    await user.click(await screen.findByRole("button", { name: "Rebuild facts" }))

    expect(
      await screen.findByText("You have tried too many times. Please try again after some time."),
    ).toBeInTheDocument()
  })
})
