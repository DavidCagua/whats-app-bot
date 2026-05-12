import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { redirect } from "next/navigation"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  AlertTriangle,
  Ban,
  Bot,
  ClipboardList,
  Clock,
  Hourglass,
  MessageSquare,
  Package,
  Percent,
  ShoppingCart,
  Tag,
  Users,
  Wallet,
  type LucideIcon,
} from "lucide-react"
import { getDashboardKpis } from "@/lib/dashboard-kpis"
import {
  detectKind,
  formatRangeLabel,
  parseRange,
} from "@/lib/orders-date-range"
import { DashboardRangePicker } from "./components/dashboard-range-picker"

interface BusinessOverviewPageProps {
  params: Promise<{ id: string }>
  searchParams: Promise<{ from?: string; to?: string }>
}

const COP_FMT = new Intl.NumberFormat("es-CO", {
  style: "currency",
  currency: "COP",
  maximumFractionDigits: 0,
})

function formatCop(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—"
  return COP_FMT.format(Math.round(n))
}

function formatPct(p: number | null | undefined): string {
  if (p === null || p === undefined) return "—"
  return `${p.toFixed(1)}%`
}

function formatMinutes(m: number | null | undefined): string {
  if (m === null || m === undefined) return "—"
  if (m < 1) return `${(m * 60).toFixed(0)} s`
  return `${m.toFixed(1)} min`
}

function formatSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—"
  if (s < 60) return `${s.toFixed(1)} s`
  return `${(s / 60).toFixed(1)} min`
}

function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—"
  return n.toLocaleString("es-CO")
}

type Kpi = {
  title: string
  value: string
  description?: string
  icon: LucideIcon
}

export default async function BusinessOverviewPage({
  params,
  searchParams,
}: BusinessOverviewPageProps) {
  const { id } = await params
  const sp = await searchParams
  const session = await auth()

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const range = parseRange({ from: sp.from, to: sp.to })
  const rangeKind = detectKind(range)
  const rangeLabel = formatRangeLabel(range, rangeKind)

  const [business, kpis] = await Promise.all([
    prisma.businesses.findUniqueOrThrow({ where: { id } }),
    getDashboardKpis(id, range),
  ])

  const { summary, performance, catalog, constants } = kpis

  const summaryKpis: Kpi[] = [
    {
      title: "Chats → pedidos",
      value: formatPct(summary.conversionPct),
      description: `${summary.orders} pedidos / ${summary.uniqueChats} chats`,
      icon: MessageSquare,
    },
    {
      title: "Pedidos automáticos vs total",
      value: `${summary.ordersBot} / ${summary.orders}`,
      description:
        summary.orders > 0
          ? `${formatPct((summary.ordersBot / summary.orders) * 100)} por el bot`
          : "Sin pedidos en el rango",
      icon: Bot,
    },
    {
      title: "Pedidos sin completar",
      value: formatPct(summary.incompletePct),
      description: "Pendientes o en curso (excluye completados/cancelados)",
      icon: ClipboardList,
    },
    {
      title: "Cancelados",
      value: formatNumber(
        summary.cancelledByBusiness +
          summary.cancelledByCustomer +
          summary.cancelledOther
      ),
      description: `Negocio ${summary.cancelledByBusiness} · Cliente ${summary.cancelledByCustomer} · Otro ${summary.cancelledOther}`,
      icon: Ban,
    },
    {
      title: "Ticket promedio sin domicilio",
      value: formatCop(summary.avgTicketNoDelivery),
      description: `Domicilio fijo: ${formatCop(constants.deliveryFeeCop)}`,
      icon: ShoppingCart,
    },
    {
      title: "Ingreso en efectivo",
      value: formatCop(summary.cashRevenue),
      description: "Pedidos completados con pago en efectivo",
      icon: Wallet,
    },
    {
      title: "Promos aplicadas",
      value: formatNumber(summary.promosCount),
      description: `${formatPct(summary.discountPctOfRevenue)} de la facturación en descuentos`,
      icon: Tag,
    },
  ]

  const perfKpis: Kpi[] = [
    {
      title: "Tiempo de preparación",
      value: formatMinutes(performance.avgPrepMin),
      description: "Promedio desde confirmado hasta listo",
      icon: Hourglass,
    },
    {
      title: "Tiempo de despacho",
      value: formatMinutes(performance.avgDispatchMin),
      description: "Entre salida y entrega (delivery)",
      icon: Clock,
    },
    {
      title: "Tiempo total de entrega",
      value: formatMinutes(performance.avgTotalDeliveryMin),
      description: "Confirmado → entregado",
      icon: Clock,
    },
    {
      title: `Pedidos con demora (> ${constants.demoraThresholdMin} min)`,
      value: formatNumber(performance.delayedCount),
      description: `Preparación supera el umbral fijo de ${constants.demoraThresholdMin} min`,
      icon: AlertTriangle,
    },
    {
      title: "Tiempo de respuesta del bot",
      value: formatSeconds(performance.avgBotResponseSec),
      description: "Promedio entre mensaje del cliente y respuesta",
      icon: Bot,
    },
    {
      title: "Tiempo en concretar pedido",
      value: formatMinutes(performance.avgTimeToOrderMin),
      description: "Primer mensaje del cliente → pedido confirmado",
      icon: Percent,
    },
  ]

  const recurringKpi: Kpi = {
    title: "Clientes recurrentes",
    value: formatNumber(catalog.recurringCustomers),
    description: `Clientes con más de 1 pedido en el rango`,
    icon: Users,
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Resumen</h1>
          <p className="text-sm text-muted-foreground">
            {business.name} — KPIs del bot y la operación
          </p>
        </div>
        <DashboardRangePicker range={range} />
      </div>

      <p className="text-xs uppercase tracking-wide text-muted-foreground">
        Rango: <span className="font-medium normal-case">{rangeLabel}</span>
      </p>

      <Section title="Conversión y ventas">
        <KpiGrid kpis={summaryKpis} />
      </Section>

      <Section title="Operación">
        <KpiGrid kpis={perfKpis} />
      </Section>

      <Section title="Catálogo y clientes">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Card className="h-full lg:col-span-2">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                Top 5 productos más pedidos
              </CardTitle>
              <Package className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              {catalog.topProducts.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Aún no hay pedidos en el rango.
                </p>
              ) : (
                <ol className="space-y-2">
                  {catalog.topProducts.map((p, idx) => (
                    <li
                      key={p.name}
                      className="flex items-center justify-between text-sm"
                    >
                      <span className="flex items-center gap-2">
                        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                          {idx + 1}
                        </span>
                        <span className="truncate">{p.name}</span>
                      </span>
                      <span className="text-muted-foreground tabular-nums">
                        {p.qty.toLocaleString("es-CO")}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>

          <Card className="h-full">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {recurringKpi.title}
              </CardTitle>
              <recurringKpi.icon className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{recurringKpi.value}</div>
              <p className="text-xs text-muted-foreground">
                {recurringKpi.description}
              </p>
            </CardContent>
          </Card>
        </div>
      </Section>

      <p className="text-xs text-muted-foreground">
        Domicilio asumido en {formatCop(constants.deliveryFeeCop)} y demora a
        partir de {constants.demoraThresholdMin} min. Algunos KPIs solo
        muestran datos a partir de la fecha de despliegue, cuando se empezaron
        a registrar los nuevos campos del pedido.
      </p>
    </div>
  )
}

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  )
}

function KpiGrid({ kpis }: { kpis: Kpi[] }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {kpis.map(({ title, value, description, icon: Icon }) => (
        <Card key={title} className="h-full">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{title}</CardTitle>
            <Icon className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{value}</div>
            {description && (
              <p className="text-xs text-muted-foreground">{description}</p>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
