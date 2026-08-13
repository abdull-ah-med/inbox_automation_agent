"use client"

import { useState } from "react"
import { format } from "date-fns"
import { CalendarIcon, FileDown } from "lucide-react"
import type { Matcher } from "react-day-picker"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
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

const formatYmdLocal = (date: Date): string => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

const parseYmdLocal = (ymd: string): Date => {
  const [year, month, day] = ymd.split("-").map(Number)
  return new Date(year, month - 1, day)
}

const addLocalDays = (date: Date, days: number): Date => {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
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

type ReportDatePickerProps = {
  id: string
  label: string
  ariaLabel: string
  value: string
  invalid?: boolean
  disabled: Matcher[]
  todayDate: Date
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelect: (date: Date | undefined) => void
}

const ReportDatePicker = ({
  id,
  label,
  ariaLabel,
  value,
  invalid = false,
  disabled,
  todayDate,
  open,
  onOpenChange,
  onSelect,
}: ReportDatePickerProps) => {
  const selected = value ? parseYmdLocal(value) : undefined

  const handleSelect = (date: Date | undefined) => {
    onSelect(date)
    onOpenChange(false)
  }

  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Popover open={open} onOpenChange={onOpenChange}>
        <PopoverTrigger
          render={
            <Button
              id={id}
              type="button"
              variant="outline"
              tabIndex={0}
              data-empty={!selected}
              aria-invalid={invalid}
              aria-label={ariaLabel}
              className="w-full justify-start text-left font-normal data-[empty=true]:text-muted-foreground"
            />
          }
        >
          <CalendarIcon aria-hidden="true" />
          {selected ? format(selected, "PPP") : <span>Pick a date</span>}
        </PopoverTrigger>
        <PopoverContent align="start" className="w-auto p-0">
          <Calendar
            mode="single"
            selected={selected}
            defaultMonth={selected ?? todayDate}
            startMonth={addLocalDays(todayDate, -365)}
            endMonth={todayDate}
            disabled={disabled}
            captionLayout="dropdown"
            onSelect={handleSelect}
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}

export const validateReportRange = (
  fromDate: string,
  toDate: string,
): string | null => {
  if (!fromDate || !toDate) {
    return "Choose a from and to date."
  }
  if (fromDate > toDate) {
    return "From date must be on or before to date."
  }
  if (inclusiveDayCount(fromDate, toDate) > MAX_WINDOW_DAYS) {
    return `Date range cannot exceed ${MAX_WINDOW_DAYS} days.`
  }
  return null
}

export const OpsReportDownload = () => {
  const [open, setOpen] = useState(false)
  const [startOpen, setStartOpen] = useState(false)
  const [endOpen, setEndOpen] = useState(false)
  const [fromDate, setFromDate] = useState("")
  const [toDate, setToDate] = useState("")
  const [downloading, setDownloading] = useState(false)
  const todayNy = formatYmdInTimezone(new Date(), REPORT_TIMEZONE)
  const todayDate = parseYmdLocal(todayNy)

  const handleOpen = () => {
    const range = defaultRange()
    setFromDate(range.from)
    setToDate(range.to)
    setStartOpen(false)
    setEndOpen(false)
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

  const handleStartSelect = (date: Date | undefined) => {
    setFromDate(date ? formatYmdLocal(date) : "")
  }

  const handleEndSelect = (date: Date | undefined) => {
    setToDate(date ? formatYmdLocal(date) : "")
  }

  const handleStartOpenChange = (nextOpen: boolean) => {
    setStartOpen(nextOpen)
    if (nextOpen) setEndOpen(false)
  }

  const handleEndOpenChange = (nextOpen: boolean) => {
    setEndOpen(nextOpen)
    if (nextOpen) setStartOpen(false)
  }

  const handleDownload = async () => {
    if (downloading) return
    const error = validateReportRange(fromDate, toDate)
    if (error) {
      toast.error(error)
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
  const startDisabled: Matcher[] = [
    { after: todayDate },
    ...(toDate
      ? [
          { after: parseYmdLocal(toDate) },
          {
            before: addLocalDays(
              parseYmdLocal(toDate),
              -(MAX_WINDOW_DAYS - 1),
            ),
          },
        ]
      : []),
  ]
  const endDisabled: Matcher[] = [
    { after: todayDate },
    ...(fromDate
      ? [
          { before: parseYmdLocal(fromDate) },
          {
            after: addLocalDays(parseYmdLocal(fromDate), MAX_WINDOW_DAYS - 1),
          },
        ]
      : []),
  ]

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
            <ReportDatePicker
              id="ops-report-start"
              label="Start"
              ariaLabel="Report start date"
              value={fromDate}
              invalid={inverted}
              disabled={startDisabled}
              todayDate={todayDate}
              open={startOpen}
              onOpenChange={handleStartOpenChange}
              onSelect={handleStartSelect}
            />
            <ReportDatePicker
              id="ops-report-end"
              label="End"
              ariaLabel="Report end date"
              value={toDate}
              invalid={inverted}
              disabled={endDisabled}
              todayDate={todayDate}
              open={endOpen}
              onOpenChange={handleEndOpenChange}
              onSelect={handleEndSelect}
            />
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
