import { prisma } from "@/lib/prisma";
import { auth } from "@/lib/auth";
import { canAccessBusiness } from "@/lib/permissions";
import { notFound, redirect } from "next/navigation";
import { format } from "date-fns";
import { PrintActions } from "./print-actions";
import { formatDisplayNumber } from "@/lib/utils";
import styles from "./print.module.css";

interface PrintOrderPageProps {
  params: Promise<{ orderId: string }>;
}

const formatCOP = (n: number) =>
  new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    minimumFractionDigits: 0,
  }).format(n);

const capitalize = (value: string | null | undefined): string => {
  if (!value) return "";
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
};

export default async function PrintOrderPage({ params }: PrintOrderPageProps) {
  const { orderId } = await params;
  const session = await auth();
  if (!session) redirect("/login");

  const order = await prisma.orders.findUnique({
    where: { id: orderId },
    include: {
      businesses: {
        include: {
          whatsapp_numbers: {
            where: { is_active: true },
            orderBy: { created_at: "asc" },
            take: 1,
          },
        },
      },
      customers: true,
      order_items: { include: { products: true } },
    },
  });

  if (!order) notFound();
  if (!canAccessBusiness(session, order.business_id)) {
    redirect("/businesses");
  }

  const business = order.businesses;
  const businessPhone = business.whatsapp_numbers[0]?.phone_number ?? null;

  const items = order.order_items.map((oi) => ({
    id: oi.id,
    quantity: oi.quantity,
    name: oi.products.name,
    notes: oi.notes,
    lineTotal: Number(oi.line_total.toString()),
  }));
  const subtotal = items.reduce((s, i) => s + i.lineTotal, 0);

  const customerName = capitalize(order.customers?.name);
  const customerWa = order.whatsapp_id ?? order.customers?.whatsapp_id ?? null;
  const customerPhone = order.contact_phone ?? order.customers?.phone ?? null;
  const deliveryAddress =
    order.delivery_address ?? order.customers?.address ?? null;

  const created = order.created_at
    ? format(new Date(order.created_at), "dd/MM/yyyy HH:mm")
    : "";

  return (
    <div className={styles.screen}>
      {/* Fallback @page rule. The real one is injected dynamically by
          PrintActions — it measures the receipt and sets an exact
          height so there's no blank tail. This static rule only kicks
          in if the JS hasn't run yet (e.g. Cmd+P before mount). */}
      <style>{`
        @page {
          size: 80mm 200mm;
          margin: 0;
        }
      `}</style>

      <div className={styles.card}>
        <div className={styles.actions}>
          <PrintActions />
        </div>

        <div className={styles.receipt} data-receipt>
          <div className={`${styles.center} ${styles.bold} ${styles.large}`}>
            {business.name.toUpperCase()}
          </div>
          {businessPhone && (
            <div className={styles.center}>{businessPhone}</div>
          )}

          <hr className={styles.divider} />

          <div className={styles.row}>
            <span className={styles.label}>Pedido</span>
            <span className={styles.value}>
              {formatDisplayNumber(order.display_number)}
            </span>
          </div>
          <div className={styles.row}>
            <span className={styles.label}>Fecha</span>
            <span className={styles.value}>{created}</span>
          </div>

          {(customerName || customerWa) && (
            <>
              <hr className={styles.divider} />
              {customerName && (
                <div className={styles.row}>
                  <span className={styles.label}>Cliente</span>
                  <span className={styles.value}>{customerName}</span>
                </div>
              )}
              {customerWa && (
                <div className={styles.row}>
                  <span className={styles.label}>WhatsApp</span>
                  <span className={styles.value}>{customerWa}</span>
                </div>
              )}
              {customerPhone && customerPhone !== customerWa && (
                <div className={styles.row}>
                  <span className={styles.label}>Tel</span>
                  <span className={styles.value}>{customerPhone}</span>
                </div>
              )}
              {deliveryAddress && (
                <div className={styles.row}>
                  <span className={styles.label}>Dir.</span>
                  <span className={styles.value}>
                    {capitalize(deliveryAddress)}
                  </span>
                </div>
              )}
            </>
          )}

          <hr className={styles.divider} />

          {items.map((item) => (
            <div key={item.id}>
              <div className={styles.itemLine}>
                <span className={styles.itemQty}>{item.quantity}×</span>
                <span className={styles.itemName}>{item.name}</span>
                <span className={styles.itemTotal}>
                  {formatCOP(item.lineTotal)}
                </span>
              </div>
              {item.notes && (
                <div className={styles.itemNote}>{item.notes}</div>
              )}
            </div>
          ))}

          <hr className={styles.divider} />

          <div className={styles.totalsRow}>
            <span>Subtotal:</span>
            <span>{formatCOP(subtotal)}</span>
          </div>
          <div className={styles.totalsRow}>
            <span>Domicilio:</span>
            <span>Por confirmar</span>
          </div>
          <div className={`${styles.totalsRow} ${styles.grandTotal}`}>
            <span>TOTAL PRODUCTOS:</span>
            <span>{formatCOP(subtotal)}</span>
          </div>

          {(order.payment_method || order.notes) && (
            <>
              <hr className={styles.divider} />
              {order.payment_method && (
                <div className={styles.row}>
                  <span className={styles.label}>Pago</span>
                  <span className={styles.value}>
                    {capitalize(order.payment_method)}
                  </span>
                </div>
              )}
              {order.notes && (
                <div className={styles.row}>
                  <span className={styles.label}>Notas</span>
                  <span className={styles.value}>{order.notes}</span>
                </div>
              )}
            </>
          )}

          <hr className={styles.divider} />

          <div className={styles.center}>¡Gracias por su compra!</div>
        </div>
      </div>
    </div>
  );
}
