import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Format an order's daily counter as ``#001`` — three-digit zero-pad as
 * the floor; numbers with more digits print as-is (``#1234``). The
 * counter resets per business at Bogotá midnight, so 999 is rare but
 * not impossible on a busy day.
 */
export function formatDisplayNumber(n: number): string {
  return `#${String(n).padStart(3, "0")}`;
}
