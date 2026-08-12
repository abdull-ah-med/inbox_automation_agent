import { fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const downloadWeekly = vi.fn()
const toastError = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    reports: {
      downloadWeekly: (...args: unknown[]) => downloadWeekly(...args),
    },
  },
}))

vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
  },
}))

import { OpsReportDownload } from "@/components/ops-report-download"

const openDialog = async () => {
  const user = userEvent.setup()
  render(<OpsReportDownload />)
  await user.click(screen.getByRole("button", { name: "Download reports" }))
  const dialog = await screen.findByRole("dialog")
  return { user, dialog }
}

describe("OpsReportDownload", () => {
  beforeEach(() => {
    downloadWeekly.mockReset()
    toastError.mockReset()
    vi.stubGlobal(
      "URL",
      {
        createObjectURL: vi.fn(() => "blob:report"),
        revokeObjectURL: vi.fn(),
      } as unknown as typeof URL,
    )
  })

  it("opens a modal from the download reports button", async () => {
    const { dialog } = await openDialog()
    expect(dialog).toHaveAttribute("data-size", "md")
    expect(within(dialog).getByText("Download reports")).toBeInTheDocument()
    expect(within(dialog).getByLabelText("Report from date")).toBeInTheDocument()
    expect(within(dialog).getByLabelText("Report to date")).toBeInTheDocument()
  })

  it("downloads the report for the selected date range", async () => {
    downloadWeekly.mockResolvedValue({
      blob: new Blob(["%PDF"], { type: "application/pdf" }),
      contentType: "application/pdf",
      filename: "ops-weekly-2026-08-03_2026-08-09.pdf",
    })
    const click = vi.fn()
    const originalCreate = document.createElement.bind(document)
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = originalCreate(tag)
      if (tag === "a") {
        el.click = click
      }
      return el
    })

    const { user, dialog } = await openDialog()
    fireEvent.change(within(dialog).getByLabelText("Report from date"), {
      target: { value: "2026-08-03" },
    })
    fireEvent.change(within(dialog).getByLabelText("Report to date"), {
      target: { value: "2026-08-09" },
    })
    await user.click(
      within(dialog).getByRole("button", { name: "Confirm download reports" }),
    )

    expect(downloadWeekly).toHaveBeenCalledWith({
      from: "2026-08-03",
      to: "2026-08-09",
    })
    expect(click).toHaveBeenCalled()
  })

  it("shows an error toast when download fails", async () => {
    downloadWeekly.mockRejectedValue(new Error("Unable to reach the server."))
    const { user, dialog } = await openDialog()
    await user.click(
      within(dialog).getByRole("button", { name: "Confirm download reports" }),
    )
    expect(toastError).toHaveBeenCalled()
  })

  it("rejects an inverted date range without calling the API", async () => {
    const { user, dialog } = await openDialog()
    fireEvent.change(within(dialog).getByLabelText("Report from date"), {
      target: { value: "2026-08-12" },
    })
    fireEvent.change(within(dialog).getByLabelText("Report to date"), {
      target: { value: "2026-08-01" },
    })
    await user.click(
      within(dialog).getByRole("button", { name: "Confirm download reports" }),
    )
    expect(downloadWeekly).not.toHaveBeenCalled()
    expect(toastError).toHaveBeenCalled()
  })

  it("rejects a range longer than 93 days without calling the API", async () => {
    const { user, dialog } = await openDialog()
    fireEvent.change(within(dialog).getByLabelText("Report from date"), {
      target: { value: "2026-01-01" },
    })
    fireEvent.change(within(dialog).getByLabelText("Report to date"), {
      target: { value: "2026-04-05" },
    })
    await user.click(
      within(dialog).getByRole("button", { name: "Confirm download reports" }),
    )
    expect(downloadWeekly).not.toHaveBeenCalled()
    expect(toastError).toHaveBeenCalled()
  })
})
