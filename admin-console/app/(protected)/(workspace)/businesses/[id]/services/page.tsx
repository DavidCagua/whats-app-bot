import { auth } from "@/lib/auth";
import { canAccessBusiness } from "@/lib/permissions";
import { redirectIfModuleDisabled } from "@/lib/modules";
import { prisma } from "@/lib/prisma";
import { redirect } from "next/navigation";
import { ServicesManager } from "./services-manager";

interface ServicesPageProps {
  params: Promise<{ id: string }>;
}

export default async function ServicesPage({ params }: ServicesPageProps) {
  const { id } = await params;
  const session = await auth();

  if (!session?.user) {
    redirect("/login");
  }
  if (!canAccessBusiness(session, id)) {
    redirect("/businesses");
  }
  await redirectIfModuleDisabled(id, "services");

  const [business, services] = await Promise.all([
    prisma.businesses.findUnique({
      where: { id },
      select: { id: true, name: true },
    }),
    prisma.services.findMany({
      where: { business_id: id },
      orderBy: [{ is_active: "desc" }, { name: "asc" }],
    }),
  ]);

  if (!business) {
    redirect("/businesses");
  }

  const mappedServices = services.map((service) => ({
    id: service.id,
    business_id: service.business_id,
    name: service.name,
    description: service.description,
    price: Number(service.price.toString()),
    currency: service.currency ?? "COP",
    duration_minutes: service.duration_minutes,
    is_active: service.is_active ?? true,
    created_at: service.created_at ? service.created_at.toISOString() : null,
    updated_at: service.updated_at ? service.updated_at.toISOString() : null,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Servicios</h1>
        <p className="text-muted-foreground mt-1">
          Catálogo de servicios para reservas de {business.name}
        </p>
      </div>

      <ServicesManager businessId={id} initialServices={mappedServices} />
    </div>
  );
}
