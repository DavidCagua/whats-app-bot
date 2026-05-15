"use client";

import { useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AlertTriangle } from "lucide-react";
import {
  CANCEL_REASONS,
  type CancelReasonKey,
  buildCancelMessage,
} from "@/lib/order-cancel-reasons";
import { formatDisplayNumber } from "@/lib/utils";

export type CancelDialogResult = {
  reasonKey: CancelReasonKey;
  otherText: string;
  notes: string;
  sendCustomerMessage: boolean;
  previewMessage: string;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Order context for the preview. */
  displayNumber: number | null | undefined;
  customerName: string | null | undefined;
  /** Called when the operator confirms. Caller persists + sends. */
  onConfirm: (result: CancelDialogResult) => Promise<void> | void;
};

function firstName(name: string | null | undefined): string | null {
  if (!name) return null;
  const trimmed = name.trim();
  if (!trimmed) return null;
  return trimmed.split(/\s+/)[0];
}

export function CancelOrderDialog({
  open,
  onOpenChange,
  displayNumber,
  customerName,
  onConfirm,
}: Props) {
  const [reasonKey, setReasonKey] = useState<CancelReasonKey | "">("");
  const [otherText, setOtherText] = useState("");
  const [notes, setNotes] = useState("");
  const [sendCustomerMessage, setSendCustomerMessage] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const isOtherInvalid = reasonKey === "other" && !otherText.trim();
  const canConfirm = !!reasonKey && !isOtherInvalid && !submitting;

  const previewMessage = useMemo(() => {
    if (!reasonKey) return "";
    return buildCancelMessage({
      displayNumber,
      customerFirstName: firstName(customerName),
      reasonKey,
      otherText,
    });
  }, [reasonKey, otherText, displayNumber, customerName]);

  const reset = () => {
    setReasonKey("");
    setOtherText("");
    setNotes("");
    setSendCustomerMessage(true);
    setSubmitting(false);
  };

  const handleOpenChange = (next: boolean) => {
    if (submitting) return;
    if (!next) reset();
    onOpenChange(next);
  };

  const handleConfirm = async () => {
    if (!canConfirm || !reasonKey) return;
    setSubmitting(true);
    try {
      await onConfirm({
        reasonKey,
        otherText: otherText.trim(),
        notes: notes.trim(),
        sendCustomerMessage,
        previewMessage,
      });
      reset();
      onOpenChange(false);
    } catch {
      // Caller surfaces error via toast; keep dialog open so operator can retry.
      setSubmitting(false);
    }
  };

  const orderLabel = displayNumber
    ? `#${formatDisplayNumber(displayNumber)}`
    : "";

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            Cancelar pedido {orderLabel}
          </DialogTitle>
          <DialogDescription>
            Selecciona un motivo. El cliente recibirá un mensaje con la
            explicación si lo dejas activado.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cancel-reason">
              Motivo <span className="text-destructive">*</span>
            </Label>
            <Select
              value={reasonKey}
              onValueChange={(v) => setReasonKey(v as CancelReasonKey)}
            >
              <SelectTrigger id="cancel-reason" className="w-full">
                <SelectValue placeholder="Selecciona un motivo…" />
              </SelectTrigger>
              <SelectContent>
                {CANCEL_REASONS.map((r) => (
                  <SelectItem key={r.key} value={r.key}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {reasonKey === "other" && (
            <div className="space-y-2">
              <Label htmlFor="cancel-other">
                Describe el motivo <span className="text-destructive">*</span>
              </Label>
              <Textarea
                id="cancel-other"
                value={otherText}
                onChange={(e) => setOtherText(e.target.value)}
                placeholder="Ej. tuvimos un problema con la caja registradora"
                rows={2}
                maxLength={200}
              />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="cancel-notes">
              Notas internas{" "}
              <span className="text-xs text-muted-foreground">(opcional)</span>
            </Label>
            <Textarea
              id="cancel-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="No se envían al cliente."
              rows={2}
              maxLength={300}
            />
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div className="flex flex-col">
              <Label htmlFor="cancel-send" className="font-medium">
                Enviar mensaje al cliente
              </Label>
              <span className="text-xs text-muted-foreground">
                Activado por defecto.
              </span>
            </div>
            <Switch
              id="cancel-send"
              checked={sendCustomerMessage}
              onCheckedChange={setSendCustomerMessage}
            />
          </div>

          {sendCustomerMessage && previewMessage && (
            <div className="rounded-md bg-muted/50 border p-3">
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Vista previa del mensaje
              </div>
              <p className="text-sm whitespace-pre-wrap">{previewMessage}</p>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={submitting}
          >
            Volver
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={handleConfirm}
            disabled={!canConfirm}
          >
            {submitting ? "Cancelando…" : "Cancelar pedido"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
