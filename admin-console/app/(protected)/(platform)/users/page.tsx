import { auth } from "@/lib/auth";
import { isSuperAdmin } from "@/lib/permissions";
import { redirect } from "next/navigation";
import { getUsers } from "@/lib/actions/users";
import { Button } from "@/components/ui/button";
import { Plus } from "lucide-react";
import Link from "next/link";
import { UsersTable } from "./components/users-table";

export default async function UsersPage() {
  const session = await auth();

  if (!isSuperAdmin(session)) {
    redirect("/");
  }

  const users = await getUsers();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Usuarios</h1>
          <p className="text-muted-foreground">
            Gestiona los usuarios del sistema y sus asignaciones de negocio
          </p>
        </div>
        <Button asChild>
          <Link href="/users/new">
            <Plus className="mr-2 h-4 w-4" />
            Agregar usuario
          </Link>
        </Button>
      </div>

      <UsersTable data={users} />
    </div>
  );
}
