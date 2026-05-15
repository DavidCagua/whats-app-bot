"use client"

import { useState, useTransition } from "react"
import { toast } from "sonner"
import { AlertTriangle, PauseCircle } from "lucide-react"

import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  ETA_DROPDOWN_OPTIONS,
  formatEtaRange,
  formatNominalEtaRange,
} from "@/lib/format-eta"
import { updateOperationsSettings } from "@/lib/actions/operations-settings"

interface OperationsControlsProps {
  businessId: string
  initialDeliveryPaused: boolean
  initialEtaMinutes: number | null
  canEdit: boolean
}

const NOMINAL_VALUE = "nominal"

export function OperationsControls({
  businessId,
  initialDeliveryPaused,
  initialEtaMinutes,
  canEdit,
}: OperationsControlsProps) {
  const [paused, setPaused] = useState(initialDeliveryPaused)
  const [etaMinutes, setEtaMinutes] = useState<number | null>(initialEtaMinutes)
  const [isPending, startTransition] = useTransition()

  const apply = (
    patch: { delivery_paused?: boolean; delivery_eta_minutes?: number | null },
    optimistic: () => void,
    revert: () => void,
    successMsg: string,
  ) => {
    optimistic()
    startTransition(async () => {
      const res = await updateOperationsSettings(businessId, patch)
      if (res.success) {
        toast.success(successMsg)
      } else {
        revert()
        toast.error(res.error || "No se pudo actualizar")
      }
    })
  }

  const onPauseChange = (next: boolean) => {
    const prev = paused
    apply(
      { delivery_paused: next },
      () => setPaused(next),
      () => setPaused(prev),
      next ? "Pedidos pausados" : "Pedidos reanudados",
    )
  }

  const onEtaChange = (value: string) => {
    const next = value === NOMINAL_VALUE ? null : Number(value)
    const prev = etaMinutes
    apply(
      { delivery_eta_minutes: next },
      () => setEtaMinutes(next),
      () => setEtaMinutes(prev),
      next === null
        ? "Tiempo de entrega restablecido"
        : `Tiempo de entrega actualizado a ${formatEtaRange(next)}`,
    )
  }

  const selectValue = etaMinutes === null ? NOMINAL_VALUE : String(etaMinutes)

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg border bg-card px-4 py-3">
      <div className="flex items-center gap-2">
        <PauseCircle className="size-4 text-muted-foreground" />
        <Label
          htmlFor="ops-pause"
          className="text-sm font-medium cursor-pointer select-none"
        >
          Pausar pedidos
        </Label>
        <Switch
          id="ops-pause"
          checked={paused}
          onCheckedChange={onPauseChange}
          disabled={!canEdit || isPending}
        />
        {paused && (
          <span className="text-xs text-amber-600 dark:text-amber-500">
            No estamos tomando pedidos
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <AlertTriangle className="size-4 text-muted-foreground" />
        <Label htmlFor="ops-eta" className="text-sm font-medium">
          Tiempo de entrega
        </Label>
        <Select
          value={selectValue}
          onValueChange={onEtaChange}
          disabled={!canEdit || isPending}
        >
          <SelectTrigger id="ops-eta" size="sm" className="min-w-[170px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NOMINAL_VALUE}>
              Normal ({formatNominalEtaRange()})
            </SelectItem>
            {ETA_DROPDOWN_OPTIONS.map((m) => (
              <SelectItem key={m} value={String(m)}>
                {formatEtaRange(m)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {etaMinutes !== null && (
          <span className="text-xs text-amber-600 dark:text-amber-500">
            Avisando demora al cliente
          </span>
        )}
      </div>
    </div>
  )
}
