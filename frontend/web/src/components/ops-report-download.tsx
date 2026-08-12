"use client"

import { useState, type ChangeEvent } from "react"
import { FileDown } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api-client"
import { getErrorMessage } from "@/lib/error-messages"

const REPORT_TIMEZONE = "America/New_York"
const MAX_WINDOW_DAYS = 93
const DEFAULT_ROLLING_DAYS = 7

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

const inclusiveDayCount = (fromYmd: string, toYmd: string): number => {
  const fromMs = Date.parse(`${fromYmd}T00:00:00Z`)
  const toMs = Date.parse(`${toYmd}T00:00:00Z`)
  return Math.floor((toMs - fromMs) / 86_400_000) + 1
}

const defaultRange = (): { from: string; to: string } => {
  const to = formatYmdInTimezone(new Date(), REPORT_TIMEZONE)
  return { from: addCalendarDays(to, -(DEFAULT_ROLLING_DAYS - 1)), to }
}

export const OpsReportDownload = () => {
  const [open, setOpen] = useState(false)
  const [fromDate, setFromDate] = useState("")
  const [toDate, setToDate] = useState("")
  const [downloading, setDownloading] = useState(false)
  const todayNy = formatYmdInTimezone(new Date(), REPORT_TIMEZONE)

  const handleOpen = () => {
    const range = defaultRange()
    setFromDate(range.from)
    setToDate(range.to)
    setOpen(true)
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      handleOpen()
      return
    }
    if (downloading) return
    setOpen(false)
  }

  const handleFromChange = (event: ChangeEvent<HTMLInputElement>) => {
    setFromDate(event.target.value)
  }

  const handleToChange = (event: ChangeEvent<HTMLInputElement>) => {
    setToDate(event.target.value)
  }

  const handleDownload = async () => {
    if (downloading) return
    if (!fromDate || !toDate) {
      toast.error("Choose a from and to date.")
      return
    }
    if (fromDate > toDate) {
      toast.error("From date must be on or before to date.")
      return
    }
    if (inclusiveDayCount(fromDate, toDate) > MAX_WINDOW_DAYS) {
      toast.error(`Date range cannot exceed ${MAX_WINDOW_DAYS} days.`)
      return
    }
    setDownloading(true)
    try {
      const { blob, filename } = await api.reports.downloadWeekly({
        from: fromDate,
        to: toDate,
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = filename ?? "weekly-ops-report.pdf"
      link.rel = "noopener"
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setOpen(false)
    } catch (error) {
      toast.error(getErrorMessage(error))
    } finally {
      setDownloading(false)
    }
  }

  const inverted = Boolean(fromDate && toDate && fromDate > toDate)

  return (
    <>
      <Button
        type="button"
        variant="outline"
        tabIndex={0}
        aria-label="Download reports"
        className="min-h-10"
        onClick={handleOpen}
      >
        <FileDown aria-hidden="true" />
        Download reports
      </Button>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Download reports</DialogTitle>
            <DialogDescription>
              SampleSite Support weekly ops report. Choose a date range in Eastern
              Time. The PDF includes the live queue even if the period is quiet.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="ops-report-from">From</Label>
              <Input
                id="ops-report-from"
                type="date"
                value={fromDate}
                max={todayNy}
                aria-invalid={inverted}
                aria-label="Report from date"
                onChange={handleFromChange}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="ops-report-to">To</Label>
              <Input
                id="ops-report-to"
                type="date"
                value={toDate}
                max={todayNy}
                aria-invalid={inverted}
                aria-label="Report to date"
                onChange={handleToChange}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel download reports"
              disabled={downloading}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Confirm download reports"
              aria-busy={downloading}
              disabled={downloading}
              onClick={() => {
                void handleDownload()
              }}
            >
              {downloading ? "Preparing report…" : "Download"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
