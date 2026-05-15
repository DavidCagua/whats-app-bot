"use server";

import { prisma } from "./prisma";
import { auth } from "./auth";
import { canEditBusiness } from "./permissions";

export type OrderEditData = {
  id: string;
  displayNumber: number;
  displayDate: string;
  businessId: string;
  status: string;
  customer: {
    customerId: number | null;
    whatsappId: string | null;
    name: string | null;
  };
  items: {
    productId: string;
    quantity: number;
    unitPrice: number;
    notes: string | null;
  }[];
  deliveryAddress: string | null;
  contactPhone: string | null;
  paymentMethod: string | null;
  fulfillmentType: "delivery" | "pickup";
  notes: string | null;
  /** Whether the order currently has any promotion records attached. UI uses
   *  this to show a "promos will be cleared" notice. */
  hasPromotions: boolean;
};

type Result =
  | { success: true; data: OrderEditData }
  | { success: false; error: string };

export async function getOrderForEdit(orderId: string): Promise<Result> {
  const session = await auth();
  if (!session?.user) return { success: false, error: "Unauthorized" };

  const order = await prisma.orders.findUnique({
    where: { id: orderId },
    include: {
      order_items: {
        select: {
          product_id: true,
          quantity: true,
          unit_price: true,
          notes: true,
          promotion_id: true,
          promo_group_id: true,
        },
      },
      order_promotions: { select: { id: true } },
      customers: { select: { id: true, whatsapp_id: true, name: true } },
    },
  });
  if (!order) return { success: false, error: "Pedido no encontrado" };
  if (!canEditBusiness(session, order.business_id)) {
    return { success: false, error: "Forbidden" };
  }

  const hasPromotions =
    order.order_promotions.length > 0 ||
    order.order_items.some(
      (i) => i.promotion_id !== null || i.promo_group_id !== null,
    );

  return {
    success: true,
    data: {
      id: order.id,
      displayNumber: order.display_number,
      displayDate: order.display_date.toISOString().slice(0, 10),
      businessId: order.business_id,
      status: order.status ?? "pending",
      customer: {
        customerId: order.customer_id ?? null,
        whatsappId: order.whatsapp_id ?? order.customers?.whatsapp_id ?? null,
        name: order.customers?.name ?? null,
      },
      items: order.order_items.map((i) => ({
        productId: i.product_id,
        quantity: i.quantity,
        unitPrice: Number(i.unit_price.toString()),
        notes: i.notes ?? null,
      })),
      deliveryAddress: order.delivery_address ?? null,
      contactPhone: order.contact_phone ?? null,
      paymentMethod: order.payment_method ?? null,
      fulfillmentType:
        order.fulfillment_type === "pickup" ? "pickup" : "delivery",
      notes: order.notes ?? null,
      hasPromotions,
    },
  };
}
