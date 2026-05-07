"use client"

import { useMemo, useState } from "react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Input } from "@/components/ui/input"
import { format } from "date-fns"
import type { CustomerRow } from "@/lib/customers-queries"

const capitalize = (value: string | null | undefined): string => {
  if (!value) return "—"
  const trimmed = value.trim()
  if (!trimmed) return "—"
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
}

const formatDate = (iso: string | null) =>
  iso ? format(new Date(iso), "MMM d, yyyy") : "—"

export function CustomersTable({
  initialCustomers,
}: {
  initialCustomers: CustomerRow[]
}) {
  const [query, setQuery] = useState("")

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return initialCustomers
    return initialCustomers.filter((c) => {
      return (
        c.name.toLowerCase().includes(q) ||
        c.whatsapp_id.toLowerCase().includes(q) ||
        (c.phone?.toLowerCase().includes(q) ?? false) ||
        (c.address?.toLowerCase().includes(q) ?? false)
      )
    })
  }, [initialCustomers, query])

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <Input
          type="search"
          placeholder="Buscar por nombre, WhatsApp, teléfono o dirección…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="max-w-sm"
        />
        <span className="text-sm text-muted-foreground">
          {filtered.length} de {initialCustomers.length}
        </span>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nombre</TableHead>
              <TableHead>WhatsApp</TableHead>
              <TableHead>Teléfono</TableHead>
              <TableHead>Dirección</TableHead>
              <TableHead className="text-right">Pedidos</TableHead>
              <TableHead>Última actividad</TableHead>
              <TableHead>Cliente desde</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="text-center text-muted-foreground py-8"
                >
                  {initialCustomers.length === 0
                    ? "Aún no tienes clientes — aparecerán cuando alguien haga un pedido o reserva."
                    : "Ningún cliente coincide con la búsqueda."}
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((c) => (
                <TableRow key={c.id}>
                  <TableCell>{capitalize(c.name)}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {c.whatsapp_id}
                  </TableCell>
                  <TableCell>{c.phone ?? "—"}</TableCell>
                  <TableCell className="max-w-[260px] whitespace-normal break-words align-top">
                    {capitalize(c.address)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {c.orders_count}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatDate(c.last_seen_at)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatDate(c.created_at)}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
