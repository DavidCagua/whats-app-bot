/**
 * Delivery-ETA label formatter.
 *
 * Operator picks a lower-bound (70 / 80 / 90 min) on the orders page;
 * the bot quotes a 10-minute range (70 → 70–80). The dashboard label
 * shows the same numbers in a compact hr/min form so operator and
 * customer always see matching values.
 *
 *   formatEtaRange(70) → "1hr 10min - 1hr 20min"
 *   formatEtaRange(60) → "1hr - 1hr 10min"
 *   formatEtaRange(90) → "1hr 30min - 1hr 40min"
 *
 * The default (no override) renders as "40min - 50min" via
 * NOMINAL_LOWER / NOMINAL_UPPER, kept in sync with order_eta.py.
 */

export const NOMINAL_LOWER_MINUTES = 40
export const NOMINAL_UPPER_MINUTES = 50
export const ETA_RANGE_WIDTH_MINUTES = 10

export const ETA_DROPDOWN_OPTIONS = [70, 80, 90] as const

export function formatEtaSingle(minutes: number): string {
  if (minutes < 60) return `${minutes}min`
  const hours = Math.floor(minutes / 60)
  const rem = minutes % 60
  if (rem === 0) return `${hours}hr`
  return `${hours}hr ${rem}min`
}

export function formatEtaRange(lowerMinutes: number): string {
  const upper = lowerMinutes + ETA_RANGE_WIDTH_MINUTES
  return `${formatEtaSingle(lowerMinutes)} - ${formatEtaSingle(upper)}`
}

export function formatNominalEtaRange(): string {
  return `${formatEtaSingle(NOMINAL_LOWER_MINUTES)} - ${formatEtaSingle(NOMINAL_UPPER_MINUTES)}`
}
