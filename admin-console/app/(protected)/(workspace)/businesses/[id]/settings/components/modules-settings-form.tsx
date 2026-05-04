"use client"

import { useState, useTransition } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { toast } from "sonner"
import { OPTIONAL_MODULES, MODULES, type ModuleKey } from "@/lib/modules"
import { updateEnabledModules } from "@/lib/actions/business-modules"

const REQUIRED_MODULES = MODULES.filter((m) => m.required)

export function ModulesSettingsForm({
  businessId,
  initialEnabledModules,
}: {
  businessId: string
  initialEnabledModules: string[]
}) {
  const [enabled, setEnabled] = useState<Set<ModuleKey>>(
    () =>
      new Set(
        initialEnabledModules.filter((k): k is ModuleKey =>
          OPTIONAL_MODULES.some((m) => m.key === k)
        )
      )
  )
  const [isPending, startTransition] = useTransition()

  const onToggle = (key: ModuleKey, next: boolean) => {
    const previous = new Set(enabled)
    const optimistic = new Set(enabled)
    if (next) optimistic.add(key)
    else optimistic.delete(key)
    setEnabled(optimistic)

    startTransition(async () => {
      const result = await updateEnabledModules(
        businessId,
        Array.from(optimistic)
      )
      if (!result.success) {
        setEnabled(previous)
        toast.error(result.error)
        return
      }
      toast.success(next ? "Módulo activado" : "Módulo desactivado")
    })
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Módulos disponibles</CardTitle>
          <CardDescription>
            Activa o desactiva las secciones del panel para este negocio. Los
            módulos desactivados se ocultan del menú lateral y bloquean el
            acceso directo por URL.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {OPTIONAL_MODULES.map((m) => {
            const checked = enabled.has(m.key)
            return (
              <div
                key={m.key}
                className="flex items-start justify-between gap-4 rounded-md border p-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{m.label}</p>
                  {m.description && (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {m.description}
                    </p>
                  )}
                </div>
                <Switch
                  checked={checked}
                  disabled={isPending}
                  onCheckedChange={(v) => onToggle(m.key, v)}
                  aria-label={`Activar ${m.label}`}
                />
              </div>
            )
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Siempre disponibles</CardTitle>
          <CardDescription>
            Estas secciones no se pueden desactivar.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            {REQUIRED_MODULES.map((m) => (
              <Badge key={m.key} variant="secondary">
                {m.label}
              </Badge>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
