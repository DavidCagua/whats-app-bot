"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MessageSquare, Settings, UserCog } from "lucide-react";
import Link from "next/link";
import { format } from "date-fns";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type Business = {
  id: string;
  name: string;
  business_type: string | null;
  is_active: boolean | null;
  created_at: Date | null;
  whatsapp_numbers: Array<{
    id: string;
    phone_number: string;
    is_active: boolean | null;
  }>;
};

interface BusinessesTableProps {
  data: Business[];
}

export function BusinessesTable({ data }: BusinessesTableProps) {
  return (
    <TooltipProvider delayDuration={300}>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nombre</TableHead>
              <TableHead>Tipo</TableHead>
              <TableHead>Números de WhatsApp</TableHead>
              <TableHead>Estado</TableHead>
              <TableHead>Creado</TableHead>
              <TableHead className="text-right">Acciones</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="text-center text-muted-foreground"
                >
                  No se encontraron negocios. Crea el primero para empezar.
                </TableCell>
              </TableRow>
            ) : (
              data.map((business) => (
                <TableRow key={business.id}>
                  <TableCell className="font-medium">
                    <Link
                      href={`/businesses/${business.id}`}
                      className="hover:underline"
                    >
                      {business.name}
                    </Link>
                  </TableCell>
                  <TableCell className="capitalize">
                    {business.business_type || "N/A"}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      {business.whatsapp_numbers.length === 0 ? (
                        <span className="text-sm text-muted-foreground">
                          Sin números
                        </span>
                      ) : (
                        business.whatsapp_numbers.map((number) => (
                          <div
                            key={number.id}
                            className="flex items-center gap-2"
                          >
                            <span className="text-sm">
                              {number.phone_number}
                            </span>
                            {number.is_active && (
                              <Badge
                                variant="secondary"
                                className="h-5 text-xs"
                              >
                                Activo
                              </Badge>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    {business.is_active ? (
                      <Badge variant="default">Activo</Badge>
                    ) : (
                      <Badge variant="secondary">Inactivo</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {business.created_at
                      ? format(new Date(business.created_at), "MMM d, yyyy")
                      : "N/A"}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            asChild
                            variant="outline"
                            size="icon"
                            aria-label="Bandeja de entrada"
                          >
                            <Link href={`/businesses/${business.id}/inbox`}>
                              <MessageSquare className="h-4 w-4" />
                            </Link>
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>Bandeja de entrada</p>
                        </TooltipContent>
                      </Tooltip>

                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            asChild
                            variant="outline"
                            size="icon"
                            aria-label="Acceso"
                          >
                            <Link href={`/businesses/${business.id}/team`}>
                              <UserCog className="h-4 w-4" />
                            </Link>
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>Acceso</p>
                        </TooltipContent>
                      </Tooltip>

                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            asChild
                            variant="outline"
                            size="icon"
                            aria-label="Configuración"
                          >
                            <Link href={`/businesses/${business.id}/settings`}>
                              <Settings className="h-4 w-4" />
                            </Link>
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>Configuración</p>
                        </TooltipContent>
                      </Tooltip>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </TooltipProvider>
  );
}
