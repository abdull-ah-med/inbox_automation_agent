import { render, screen, within } from "@testing-library/react"
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

import {
  OpsReportDownload,
  validateReportRange,
} from "@/components/ops-report-download"

const formatYmdInTimezone = (date: Date, timeZone: string): string => {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date)
}

const addCalendarDays = (ymd: string, days: number): string => {
  const [year, month, day] = ymd.split("-").map(Number)
  const utc = new Date(Date.UTC(year, month - 1, day + days))
  return utc.toISOString().slice(0, 10)
}

const expectedDefaultRange = () => {
  const to = formatYmdInTimezone(new Date(), "America/New_York")
  return { from: addCalendarDays(to, -6), to }
}

const openDialog = async () => {
  const user = userEvent.setup()
  render(<OpsReportDownload />)
  await user.click(screen.getByRole("button", { name: "Download reports" }))
  const dialog = await screen.findByRole("dialog")
  return { user, dialog }
}

describe("validateReportRange", () => {
  it("rejects an inverted date range", () => {
    expect(validateReportRange("2026-08-12", "2026-08-01")).toMatch(/on or before/)
  })

  it("rejects a range longer than 93 days", () => {
    expect(validateReportRange("2026-01-01", "2026-04-05")).toMatch(/93 days/)
  })

  it("accepts a week-long range", () => {
    expect(validateReportRange("2026-08-03", "2026-08-09")).toBeNull()
  })
})

describe("OpsReportDownload", () => {
  beforeEach(() => {
    downloadWeekly.mockReset()
    toastError.mockReset()
    vi.restoreAllMocks()
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
    expect(
      within(dialog).getByRole("button", { name: "Report start date" }),
    ).toBeInTheDocument()
    expect(
      within(dialog).getByRole("button", { name: "Report end date" }),
    ).toBeInTheDocument()
    expect(
      within(dialog).getByRole("button", { name: "Last 7 days" }),
    ).toHaveAttribute("aria-pressed", "true")
  })

  it("applies a 30-day preset before download", async () => {
    downloadWeekly.mockResolvedValue({
      blob: new Blob(["%PDF"], { type: "application/pdf" }),
      contentType: "application/pdf",
      filename: "ops-weekly.pdf",
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
    await user.click(within(dialog).getByRole("button", { name: "Last 30 days" }))
    await user.click(
      within(dialog).getByRole("button", { name: "Confirm download reports" }),
    )

    const to = formatYmdInTimezone(new Date(), "America/New_York")
    expect(downloadWeekly).toHaveBeenCalledWith({
      from: addCalendarDays(to, -29),
      to,
    })
  })

  it("opens the start and end date calendars separately", async () => {
    const { user, dialog } = await openDialog()
    await user.click(
      within(dialog).getByRole("button", { name: "Report start date" }),
    )
    expect(screen.getByRole("grid")).toBeInTheDocument()
    await user.click(
      within(dialog).getByRole("button", { name: "Report end date" }),
    )
    expect(screen.getAllByRole("grid")).toHaveLength(1)
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
    await user.click(
      within(dialog).getByRole("button", { name: "Confirm download reports" }),
    )

    expect(downloadWeekly).toHaveBeenCalledWith(expectedDefaultRange())
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
})
