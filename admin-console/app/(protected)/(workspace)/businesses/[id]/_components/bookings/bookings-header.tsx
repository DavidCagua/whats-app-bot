import { CalendarDays } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Booking } from "@/lib/bookings-queries"

interface BookingsHeaderProps {
  bookings: Booking[]
}

export function BookingsHeader({ bookings }: BookingsHeaderProps) {
  const total = bookings.length
  const confirmed = bookings.filter((b) => b.status === "confirmed").length
  const pending = bookings.filter((b) => b.status === "pending").length
  const cancelled = bookings.filter((b) => b.status === "cancelled").length
  const completed = bookings.filter((b) => b.status === "completed").length

  const stats = [
    { label: "Total", value: total, color: "text-foreground" },
    { label: "Confirmadas", value: confirmed, color: "text-green-600" },
    { label: "Pendientes", value: pending, color: "text-yellow-600" },
    { label: "Canceladas", value: cancelled, color: "text-gray-500" },
    { label: "Completadas", value: completed, color: "text-blue-600" },
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <CalendarDays className="h-6 w-6" />
        <div>
          <h1 className="text-2xl font-bold">Reservas</h1>
          <p className="text-muted-foreground text-sm">
            Gestiona citas y disponibilidad para esta semana
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {stats.map((stat) => (
          <Card key={stat.label}>
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">{stat.label}</p>
              <p className={`text-2xl font-bold ${stat.color}`}>{stat.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
