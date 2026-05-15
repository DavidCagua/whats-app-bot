// Payment context strings used by the bot's payment_config helpers.
// Cartesian product of (fulfillment × timing). on_site covers both
// pickup and dine-in.
//
// Kept in a non-"use server" module so client components can import
// the constants and types alongside the server action.
export const PAYMENT_CONTEXTS = [
  "delivery_pay_now",
  "delivery_on_fulfillment",
  "on_site_pay_now",
  "on_site_on_fulfillment",
] as const

export type PaymentContext = (typeof PAYMENT_CONTEXTS)[number]

export type PaymentMethodConfig = {
  name: string
  contexts: PaymentContext[]
}
