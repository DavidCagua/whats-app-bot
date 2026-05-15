// Preset cancellation reasons + WhatsApp message templates.
//
// Operators pick one from a dropdown in the cancel-order dialog; the
// chosen key gets persisted on orders.cancellation_reason (prefixed with
// "admin:" so analytics can tell admin cancellations apart from bot-side
// ones tagged "customer_whatsapp"). The dialog renders a live preview
// of the apology message; if "Enviar mensaje al cliente" is on, that
// exact text is sent via /api/conversations/send.

export type CancelReasonKey =
  | "out_of_stock"
  | "no_driver"
  | "out_of_zone"
  | "customer_request"
  | "customer_unreachable"
  | "closed"
  | "other";

export type CancelReason = {
  key: CancelReasonKey;
  label: string;
  /** Apology sentence injected into the WhatsApp template. */
  apology: string;
};

export const CANCEL_REASONS: CancelReason[] = [
  {
    key: "out_of_stock",
    label: "Producto agotado",
    apology: "el producto se nos agotó en este momento",
  },
  {
    key: "no_driver",
    label: "Sin domiciliario disponible",
    apology: "no contamos con domiciliario disponible para tu zona",
  },
  {
    key: "out_of_zone",
    label: "Fuera de zona de domicilio",
    apology: "la dirección queda fuera de nuestra zona de cobertura",
  },
  {
    key: "customer_request",
    label: "Cliente solicitó cancelar",
    apology: "atendimos tu solicitud de cancelación",
  },
  {
    key: "customer_unreachable",
    label: "Cliente no responde",
    apology: "no pudimos contactarte para confirmar el pedido",
  },
  {
    key: "closed",
    label: "Cerrado / fuera de horario",
    apology: "el pedido cayó fuera de nuestro horario de atención",
  },
  {
    key: "other",
    label: "Otro",
    // Filled in by operator free text.
    apology: "",
  },
];

export const CANCEL_REASON_KEYS: CancelReasonKey[] = CANCEL_REASONS.map(
  (r) => r.key,
);

export function getCancelReason(key: CancelReasonKey): CancelReason {
  return (
    CANCEL_REASONS.find((r) => r.key === key) ??
    CANCEL_REASONS[CANCEL_REASONS.length - 1]
  );
}

/**
 * Build the WhatsApp apology message a customer receives when an
 * operator cancels their order. Tone: cordial, apologetic, leaves
 * the door open for a future order.
 *
 * @param displayNumber e.g. 1 → "#001"
 * @param customerFirstName "Yuli" → "Hola Yuli, ..."; null → "Hola, ..."
 * @param reasonKey one of CancelReasonKey
 * @param otherText required when reasonKey === "other"
 */
export function buildCancelMessage(args: {
  displayNumber: number | null | undefined;
  customerFirstName: string | null | undefined;
  reasonKey: CancelReasonKey;
  otherText?: string;
}): string {
  const { displayNumber, customerFirstName, reasonKey, otherText } = args;
  const reason = getCancelReason(reasonKey);
  const apology =
    reasonKey === "other" ? (otherText || "").trim() : reason.apology;
  const number = displayNumber
    ? ` #${String(displayNumber).padStart(3, "0")}`
    : "";
  const opener = customerFirstName ? `Hola ${customerFirstName}, ` : "Hola, ";
  const apologyClause = apology ? ` Motivo: ${apology}.` : "";
  return (
    `${opener}lamentamos mucho informarte que tu pedido${number} fue cancelado.` +
    apologyClause +
    " ¡Mil disculpas! Cuando quieras volver a pedir, aquí estamos."
  );
}

/** Tag stored on orders.cancellation_reason so analytics can group. */
export function formatStoredReason(args: {
  reasonKey: CancelReasonKey;
  otherText?: string;
  notes?: string;
}): string {
  const { reasonKey, otherText, notes } = args;
  const reason = getCancelReason(reasonKey);
  const label =
    reasonKey === "other" ? (otherText || "").trim() || "Otro" : reason.label;
  const notesPart = notes && notes.trim() ? ` | notas: ${notes.trim()}` : "";
  return `admin:${reasonKey}: ${label}${notesPart}`;
}
