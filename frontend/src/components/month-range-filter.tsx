import { useEffect, useState } from 'react'
import { parseISO } from 'date-fns'
import { useTranslation } from 'react-i18next'
import { currentMonth, monthFromRange, monthLabel, monthRange, shiftMonth } from '@/lib/month-utils'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Label } from '@/components/ui/label'

interface MonthRangeFilterProps {
  /** Active "from" date as `"YYYY-MM-DD"` (always set). */
  from: string
  /** Active "to" date as `"YYYY-MM-DD"` (always set). */
  to: string
  /** Called whenever the range changes (stepping or picking a date). */
  onChange: (from: string, to: string) => void
  /** BCP-47 locale for the month label (e.g. "pt-BR", "en-US"). */
  locale?: string
  /** Accessible labels for the prev/next buttons. */
  prevLabel?: string
  nextLabel?: string
}

/**
 * Calendar-month period filter for the account pages: `‹  Month Year  ›` steps
 * whole months, and the popover exposes De/Até date pickers clamped to the
 * active month (`minDate`/`maxDate`), so the range never leaves the calendar
 * month. A full-month `from`/`to` pair is reflected back into the stepper;
 * custom sub-ranges keep the current month label.
 */
export function MonthRangeFilter({ from, to, onChange, locale = 'pt-BR', prevLabel, nextLabel }: MonthRangeFilterProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [activeMonth, setActiveMonth] = useState(() => monthFromRange(from, to) ?? currentMonth())

  // Reflect full-month ranges set externally (e.g. a "clear filters" reset or
  // an in-progress cycle landing) back into the stepper. Non-full-month ranges
  // (e.g. a bill bar spanning a cycle across months) are labelled by the
  // range's end date — matching the bill's due month.
  useEffect(() => {
    const full = monthFromRange(from, to)
    if (full) {
      setActiveMonth(full)
    } else if (to && to.length >= 7) {
      setActiveMonth(to.slice(0, 7))
    }
  }, [from, to])

  const applyMonth = (ym: string) => {
    setActiveMonth(ym)
    const { from: f, to: t2 } = monthRange(ym)
    onChange(f, t2)
  }

  const monthBounds = monthRange(activeMonth)
  const minDate = parseISO(monthBounds.from + 'T00:00:00')
  const maxDate = parseISO(monthBounds.to + 'T00:00:00')
  // When the parent range lands outside the active month (e.g. a bill-bar
  // click spanning a cycle), show the month bounds in the pickers so the
  // clamped selection stays consistent.
  const fromInMonth = from.slice(0, 7) === activeMonth ? from : monthBounds.from
  const toInMonth = to.slice(0, 7) === activeMonth ? to : monthBounds.to
  const label = monthLabel(activeMonth, locale).replace(/^\w/, (c) => c.toUpperCase())

  return (
    <div className="flex items-center gap-1 min-w-0">
      <button
        type="button"
        aria-label={prevLabel}
        title={prevLabel}
        className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground transition-all text-base cursor-pointer"
        onClick={() => applyMonth(shiftMonth(activeMonth, -1))}
      >
        &#8249;
      </button>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="inline-flex items-center justify-center border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground min-w-0 sm:min-w-[160px] truncate hover:bg-muted/50 transition-all cursor-pointer"
          >
            {label}
          </button>
        </PopoverTrigger>
        <PopoverContent align="center" className="w-auto p-3 space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('transactions.from')}</Label>
            <DatePickerInput
              value={fromInMonth}
              onChange={(v) => onChange(v, toInMonth)}
              minDate={minDate}
              maxDate={maxDate}
              placeholder={t('transactions.from')}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">{t('transactions.to')}</Label>
            <DatePickerInput
              value={toInMonth}
              onChange={(v) => onChange(fromInMonth, v)}
              minDate={minDate}
              maxDate={maxDate}
              placeholder={t('transactions.to')}
            />
          </div>
        </PopoverContent>
      </Popover>
      <button
        type="button"
        aria-label={nextLabel}
        title={nextLabel}
        className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground transition-all text-base cursor-pointer"
        onClick={() => applyMonth(shiftMonth(activeMonth, 1))}
      >
        &#8250;
      </button>
    </div>
  )
}
