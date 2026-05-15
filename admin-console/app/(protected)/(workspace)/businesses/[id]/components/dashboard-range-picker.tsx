"use client";

import { useRouter } from "next/navigation";
import { DateRangePicker } from "@/components/date-range-picker";
import type { DateRange } from "@/lib/orders-date-range";

/**
 * Thin client wrapper around <DateRangePicker> for the dashboard page.
 *
 * Lives next to the page so the server component can render the picker
 * without itself becoming a client component. Pushing the chosen range
 * to the URL re-runs the page on the server with the new searchParams,
 * which is how the KPIs re-query.
 */
export function DashboardRangePicker({ range }: { range: DateRange }) {
  const router = useRouter();

  const handleChange = (next: DateRange) => {
    const params = new URLSearchParams({ from: next.from, to: next.to });
    router.replace(`?${params.toString()}`, { scroll: false });
  };

  return <DateRangePicker range={range} onChange={handleChange} />;
}
