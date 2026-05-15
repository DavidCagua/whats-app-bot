/**
 * Bogotá-local date range helpers for the orders page filter UI. Bogotá
 * is UTC-5 year-round (no DST), which makes the offset arithmetic stable
 * without pulling in a TZ database.
 *
 * The URL contract is `?from=YYYY-MM-DD&to=YYYY-MM-DD` (inclusive,
 * Bogotá-local). The helpers in this file are the single source of
 * truth for parsing that contract and turning it into UTC instants the
 * Prisma layer can use.
 */

const BOGOTA_OFFSET_HOURS = -5;

export type RangePreset = "today" | "yesterday" | "week" | "month";
export type RangeKind = RangePreset | "custom";

export type DateRange = {
  /** Bogotá-local date string `YYYY-MM-DD` (inclusive lower bound). */
  from: string;
  /** Bogotá-local date string `YYYY-MM-DD` (inclusive upper bound). */
  to: string;
};

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

const pad = (n: number) => n.toString().padStart(2, "0");

/** Convert a UTC instant to a Bogotá-local `YYYY-MM-DD`. */
function toBogotaDate(instant: Date): string {
  const shifted = new Date(
    instant.getTime() + BOGOTA_OFFSET_HOURS * 3600 * 1000,
  );
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(
    shifted.getUTCDate(),
  )}`;
}

/** `YYYY-MM-DD` → today/yesterday/week-start/month-start arithmetic. */
function shiftDate(date: string, days: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`;
}

/** Bogotá-local "today" as `YYYY-MM-DD`. */
export function todayBogota(now: Date = new Date()): string {
  return toBogotaDate(now);
}

/** Resolve a preset to a concrete inclusive `{ from, to }` Bogotá range. */
export function presetRange(
  preset: RangePreset,
  now: Date = new Date(),
): DateRange {
  const today = todayBogota(now);
  switch (preset) {
    case "today":
      return { from: today, to: today };
    case "yesterday": {
      const y = shiftDate(today, -1);
      return { from: y, to: y };
    }
    case "week": {
      // Monday-led week, ending today (or earlier in the week).
      const [yy, mm, dd] = today.split("-").map(Number);
      const dt = new Date(Date.UTC(yy, mm - 1, dd));
      const dow = dt.getUTCDay(); // 0=Sun, 1=Mon, ..., 6=Sat
      const daysSinceMonday = (dow + 6) % 7;
      return { from: shiftDate(today, -daysSinceMonday), to: today };
    }
    case "month": {
      const [yy, mm] = today.split("-").map(Number);
      return { from: `${yy}-${pad(mm)}-01`, to: today };
    }
  }
}

/** Detect which preset (if any) matches a range. */
export function detectKind(
  range: DateRange,
  now: Date = new Date(),
): RangeKind {
  const presets: RangePreset[] = ["today", "yesterday", "week", "month"];
  for (const p of presets) {
    const r = presetRange(p, now);
    if (r.from === range.from && r.to === range.to) return p;
  }
  return "custom";
}

/** Parse `searchParams` values into a sane Bogotá range. Defaults to today. */
export function parseRange(
  raw: { from?: string | null; to?: string | null } | null | undefined,
  now: Date = new Date(),
): DateRange {
  const from = raw?.from && ISO_DATE_RE.test(raw.from) ? raw.from : null;
  const to = raw?.to && ISO_DATE_RE.test(raw.to) ? raw.to : null;
  if (from && to) {
    // Swap if user inverted the range.
    return from <= to ? { from, to } : { from: to, to: from };
  }
  if (from) return { from, to: from };
  if (to) return { from: to, to };
  return presetRange("today", now);
}

/** Convert a Bogotá-local inclusive range to UTC instants for SQL. */
export function rangeToUtc(range: DateRange): { fromUtc: Date; toUtc: Date } {
  const offset = `${BOGOTA_OFFSET_HOURS < 0 ? "-" : "+"}${pad(
    Math.abs(BOGOTA_OFFSET_HOURS),
  )}:00`;
  return {
    fromUtc: new Date(`${range.from}T00:00:00.000${offset}`),
    toUtc: new Date(`${range.to}T23:59:59.999${offset}`),
  };
}

export function shiftRangeByDays(range: DateRange, days: number): DateRange {
  return { from: shiftDate(range.from, days), to: shiftDate(range.to, days) };
}

/** Human-readable label for the toolbar (e.g. "Hoy · 6 may"). */
export function formatRangeLabel(range: DateRange, kind: RangeKind): string {
  const formatDay = (d: string): string => {
    const [y, m, dd] = d.split("-").map(Number);
    const monthsEs = [
      "ene",
      "feb",
      "mar",
      "abr",
      "may",
      "jun",
      "jul",
      "ago",
      "sep",
      "oct",
      "nov",
      "dic",
    ];
    return `${dd} ${monthsEs[m - 1]}${
      y !== new Date().getUTCFullYear() ? ` ${y}` : ""
    }`;
  };
  if (range.from === range.to) return formatDay(range.from);
  return `${formatDay(range.from)} – ${formatDay(range.to)}`;
}
