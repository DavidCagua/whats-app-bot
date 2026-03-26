"use client"

import { useState } from "react"
import { AvailabilityRule } from "@/lib/bookings-queries"
import { saveAvailability } from "@/lib/actions/availability"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Loader2, Save } from "lucide-react"

const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

type DayRule = {
  day_of_week: number
  is_active: boolean
  open_time: string
  close_time: string
  slot_duration_minutes: number
}

function buildDefaultRules(existing: AvailabilityRule[]): DayRule[] {
  return Array.from({ length: 7 }, (_, i) => {
    const found = existing.find((r) => r.day_of_week === i)
    return {
      day_of_week: i,
      is_active: found?.is_active ?? (i !== 0 && i !== 6), // Mon-Fri active by default
      open_time: found?.open_time ?? "09:00",
      close_time: found?.close_time ?? "17:00",
      slot_duration_minutes: found?.slot_duration_minutes ?? 60,
    }
  })
}

interface AvailabilitySettingsProps {
  businessId: string
  initialRules: AvailabilityRule[]
  onRulesUpdated: (rules: AvailabilityRule[]) => void
  /** Panel / drawer layout: no outer Card, sticky save footer */
  embedded?: boolean
}

export function AvailabilitySettings({
  businessId,
  initialRules,
  onRulesUpdated,
  embedded = false,
}: AvailabilitySettingsProps) {
  const [rules, setRules] = useState<DayRule[]>(() => buildDefaultRules(initialRules))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  function updateRule(dayOfWeek: number, patch: Partial<DayRule>) {
    setRules((prev) =>
      prev.map((r) => (r.day_of_week === dayOfWeek ? { ...r, ...patch } : r))
    )
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      const result = await saveAvailability(businessId, rules)
      if (!result.success) throw new Error(result.error)
      onRulesUpdated(result.rules)
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error")
    } finally {
      setSaving(false)
    }
  }

  const rulesEditor = (
    <>
      <div className="hidden sm:grid grid-cols-[120px_64px_100px_100px_120px] gap-2 text-xs text-muted-foreground font-medium">
        <span>Day</span>
        <span>Active</span>
        <span>Opens</span>
        <span>Closes</span>
        <span>Slot (min)</span>
      </div>

      <Separator className="hidden sm:block" />

      {rules.map((rule) => (
        <div
          key={rule.day_of_week}
          className={`grid sm:grid-cols-[120px_64px_100px_100px_120px] grid-cols-2 gap-2 items-center py-1 ${
            !rule.is_active ? "opacity-50" : ""
          }`}
        >
          <Label className="font-medium col-span-2 sm:col-span-1">
            {DAY_NAMES[rule.day_of_week]}
          </Label>

          <div className="flex items-center gap-2 sm:col-span-1">
            <Switch
              checked={rule.is_active}
              onCheckedChange={(checked) =>
                updateRule(rule.day_of_week, { is_active: checked })
              }
            />
            <span className="text-xs sm:hidden text-muted-foreground">
              {rule.is_active ? "Active" : "Closed"}
            </span>
          </div>

          <div className="space-y-0.5 sm:space-y-0">
            <Label className="text-xs sm:hidden text-muted-foreground">Opens</Label>
            <Input
              type="time"
              value={rule.open_time}
              disabled={!rule.is_active}
              onChange={(e) => updateRule(rule.day_of_week, { open_time: e.target.value })}
              className="h-8 text-sm"
            />
          </div>

          <div className="space-y-0.5 sm:space-y-0">
            <Label className="text-xs sm:hidden text-muted-foreground">Closes</Label>
            <Input
              type="time"
              value={rule.close_time}
              disabled={!rule.is_active}
              onChange={(e) => updateRule(rule.day_of_week, { close_time: e.target.value })}
              className="h-8 text-sm"
            />
          </div>

          <div className="space-y-0.5 sm:space-y-0">
            <Label className="text-xs sm:hidden text-muted-foreground">Slot duration (min)</Label>
            <Input
              type="number"
              min={15}
              max={240}
              step={15}
              value={rule.slot_duration_minutes}
              disabled={!rule.is_active}
              onChange={(e) =>
                updateRule(rule.day_of_week, {
                  slot_duration_minutes: parseInt(e.target.value, 10) || 60,
                })
              }
              className="h-8 text-sm"
            />
          </div>
        </div>
      ))}
    </>
  )

  const saveRow = (
    <div className="flex flex-wrap items-center gap-3">
      <Button onClick={handleSave} disabled={saving} size="sm">
        {saving ? (
          <>
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            Saving…
          </>
        ) : (
          <>
            <Save className="h-4 w-4 mr-2" />
            Save Hours
          </>
        )}
      </Button>

      {success && (
        <span className="text-sm text-green-600">✓ Saved successfully</span>
      )}
      {error && (
        <span className="text-sm text-destructive">{error}</span>
      )}
    </div>
  )

  if (embedded) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 pb-2 pt-1">
          {rulesEditor}
        </div>
        <div className="shrink-0 border-t bg-background p-4">
          {saveRow}
        </div>
      </div>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Business Hours &amp; Availability</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {rulesEditor}
        <Separator />
        {saveRow}
      </CardContent>
    </Card>
  )
}
