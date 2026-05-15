import { prisma } from "@/lib/prisma";
import { auth } from "@/lib/auth";
import { canAccessBusiness } from "@/lib/permissions";
import { redirectIfModuleDisabled } from "@/lib/modules";
import { notFound, redirect } from "next/navigation";
import { CustomersTable } from "./components/customers-table";
import { CreateCustomerDialog } from "./components/create-customer-dialog";
import { getCustomersForBusiness } from "@/lib/customers-queries";

interface CustomersPageProps {
  params: Promise<{ id: string }>;
}

export default async function CustomersPage({ params }: CustomersPageProps) {
  const { id } = await params;
  const session = await auth();

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses");
  }
  await redirectIfModuleDisabled(id, "customers");

  const business = await prisma.businesses.findUnique({ where: { id } });
  if (!business) notFound();

  const initialCustomers = await getCustomersForBusiness(id);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Clientes</h2>
          <p className="text-sm text-muted-foreground">
            Clientes de {business.name}
          </p>
        </div>
        <CreateCustomerDialog businessId={id} />
      </div>

      <CustomersTable businessId={id} initialCustomers={initialCustomers} />
    </div>
  );
}
