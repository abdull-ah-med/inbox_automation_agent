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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { api } from "@/lib/api-client"
import {
  REPORT_TIMEZONE,
  addCalendarDays,
  addLocalDays,
  formatYmdInTimezone,
  formatYmdLocal,
  parseYmdLocal,
} from "@/lib/calendar-dates"
import { getErrorMessage } from "@/lib/error-messages"

const MAX_WINDOW_DAYS = 93
const DEFAULT_ROLLING_DAYS = 7
const PRESET_DAYS = [7, 14, 30, 60] as const

const inclusiveDayCount = (fromYmd: string, toYmd: string): number => {
  const fromMs = Date.parse(`${fromYmd}T00:00:00Z`)
  const toMs = Date.parse(`${toYmd}T00:00:00Z`)
  return Math.floor((toMs - fromMs) / 86_400_000) + 1
}

const rollingRange = (days: number): { from: string; to: string } => {
  const to = formatYmdInTimezone(new Date(), REPORT_TIMEZONE)
  return { from: addCalendarDays(to, -(days - 1)), to }
}

const defaultRange = (): { from: string; to: string } => {
  return rollingRange(DEFAULT_ROLLING_DAYS)
}

type ReportDatePickerProps = {
  id: string
  label: string
  ariaLabel: string
  value: string
  invalid?: boolean
  /** Inline filter-row mode: no floating label; empty state uses ``label``. */
  compact?: boolean
  disabled: Matcher[]
  todayDate: Date
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelect: (date: Date | undefined) => void
}

export const ReportDatePicker = ({
  id,
  label,
  ariaLabel,
  value,
  invalid = false,
  compact = false,
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

  const trigger = (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger
        render={
          <Button
            id={id}
            type="button"
            variant="outline"
            size="sm"
            tabIndex={0}
            data-empty={!selected}
            data-invalid={invalid || undefined}
            aria-label={ariaLabel}
            className={
              compact
                ? "data-[empty=true]:text-muted-foreground data-[invalid=true]:border-destructive min-h-10 w-fit justify-start text-left font-normal"
                : "data-[empty=true]:text-muted-foreground data-[invalid=true]:border-destructive min-h-10 w-full justify-start text-left font-normal"
            }
          />
        }
      >
        <CalendarIcon aria-hidden="true" />
        {selected ? format(selected, "PPP") : <span>{compact ? label : "Pick a date"}</span>}
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
  )

  if (compact) return trigger

  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {trigger}
    </div>
  )
}

export const validateReportRange = (fromDate: string, toDate: string): string | null => {
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

  const handlePresetClick = (days: number) => {
    const range = rollingRange(days)
    setFromDate(range.from)
    setToDate(range.to)
    setStartOpen(false)
    setEndOpen(false)
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
    } catch (downloadError) {
      toast.error(getErrorMessage(downloadError))
    } finally {
      setDownloading(false)
    }
  }

  const selectedPreset = PRESET_DAYS.find((days) => {
    const range = rollingRange(days)
    return range.from === fromDate && range.to === toDate
  })
  const inverted = Boolean(fromDate && toDate && fromDate > toDate)
  const startDisabled: Matcher[] = [
    { after: todayDate },
    ...(toDate
      ? [
          { after: parseYmdLocal(toDate) },
          {
            before: addLocalDays(parseYmdLocal(toDate), -(MAX_WINDOW_DAYS - 1)),
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
              SampleSite Support weekly ops report. Choose a date range in Eastern Time. The PDF
              includes the live queue even if the period is quiet.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label id="ops-report-presets-label">Last</Label>
            <div
              role="group"
              aria-labelledby="ops-report-presets-label"
              className="flex flex-wrap gap-2"
            >
              {PRESET_DAYS.map((days) => {
                const selected = selectedPreset === days
                return (
                  <Button
                    key={days}
                    type="button"
                    size="sm"
                    variant={selected ? "default" : "outline"}
                    tabIndex={0}
                    aria-pressed={selected}
                    aria-label={`Last ${days} days`}
                    disabled={downloading}
                    className="min-h-8"
                    onClick={() => handlePresetClick(days)}
                  >
                    {days} days
                  </Button>
                )
              })}
            </div>
          </div>
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
