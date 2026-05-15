"use client";

import { useState } from "react";
import { CalendarIcon } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  type DateRange,
  type RangePreset,
  detectKind,
  formatRangeLabel,
  presetRange,
} from "@/lib/orders-date-range";

// `YYYY-MM-DD` <-> Date conversion that ignores TZ — the calendar
// operates on calendar days, not instants, so we want the same y-m-d
// regardless of where the browser thinks it is.
function dateFromIso(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function isoFromDate(date: Date): string {
  const y = date.getFullYear();
  const m = (date.getMonth() + 1).toString().padStart(2, "0");
  const d = date.getDate().toString().padStart(2, "0");
  return `${y}-${m}-${d}`;
}

const PRESETS: { key: RangePreset; label: string }[] = [
  { key: "today", label: "Hoy" },
  { key: "yesterday", label: "Ayer" },
  { key: "week", label: "Semana" },
  { key: "month", label: "Mes" },
];

type Props = {
  range: DateRange;
  onChange: (next: DateRange) => void;
  /** Show only the formatted range; trigger sizing is compact by default. */
  className?: string;
};

/**
 * Range picker for the admin dashboard. Presets + calendar in range mode,
 * both inside a Popover so the toolbar stays compact.
 *
 * Mirrors the conventions on the orders page (presets keyed to the same
 * RangePreset values, same Bogotá-local URL contract) but renders true
 * calendar range selection instead of the orders page's native date input.
 * Once we're happy with the UX here we can retrofit the orders page.
 */
export function DateRangePicker({ range, onChange, className }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRange>(range);
  const kind = detectKind(range);

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (next) setDraft(range);
  };

  const handlePreset = (preset: RangePreset) => {
    const r = presetRange(preset);
    onChange(r);
    setOpen(false);
  };

  // react-day-picker calls this on every click — when both dates are
  // present we commit and close; when only one is picked we hold it
  // in `draft` and wait for the second click.
  const handleSelect = (selected: { from?: Date; to?: Date } | undefined) => {
    if (!selected?.from) {
      setDraft(range);
      return;
    }
    if (!selected.to) {
      const iso = isoFromDate(selected.from);
      setDraft({ from: iso, to: iso });
      return;
    }
    const next: DateRange = {
      from: isoFromDate(selected.from),
      to: isoFromDate(selected.to),
    };
    setDraft(next);
    onChange(next);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn("gap-2", className)}
        >
          <CalendarIcon className="h-4 w-4" />
          <span>{formatRangeLabel(range, kind)}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="end">
        <div className="flex flex-col gap-3 p-3 sm:flex-row">
          <div className="flex flex-col gap-1 sm:w-32">
            {PRESETS.map((p) => (
              <Button
                key={p.key}
                type="button"
                size="sm"
                variant={kind === p.key ? "default" : "ghost"}
                className="justify-start"
                onClick={() => handlePreset(p.key)}
              >
                {p.label}
              </Button>
            ))}
          </div>
          <div className="border-t sm:border-t-0 sm:border-l">
            <Calendar
              mode="range"
              numberOfMonths={2}
              defaultMonth={dateFromIso(draft.from)}
              selected={{
                from: dateFromIso(draft.from),
                to: dateFromIso(draft.to),
              }}
              onSelect={handleSelect}
            />
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
