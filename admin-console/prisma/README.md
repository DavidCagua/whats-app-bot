# Prisma en admin-console

## Rol de Prisma aqui

Prisma aqui es **solo un cliente de base de datos** — NO es el dueño de las migraciones.

- ✅ `prisma generate` — genera el cliente TypeScript (esto es lo único que debe correr automáticamente)
- ✅ `prisma db pull` — introspecta la DB y actualiza `schema.prisma` (usar después de una migración de Python)
- ❌ `prisma db push` — **NUNCA** — sobreescribe la DB con el schema local, puede ser destructivo
- ❌ `prisma migrate dev` — **NUNCA** — crea y aplica migraciones de Prisma, entra en conflicto con Python
- ❌ `prisma migrate reset` — **NUNCA** — borra toda la base de datos

## Dueño de migraciones

Las migraciones las maneja **Python**, con archivos SQL numerados en `/migrations/`:

```
migrations/
  001_multi_tenant_schema.sql
  002_super_admin_role.sql
  ...
```

Para aplicar una nueva migración, un dev de Python corre el SQL contra la DB correspondiente
(local o producción según el entorno).

## Workflow cuando hay cambios de schema

1. Dev de Python agrega un nuevo archivo `NNN_descripcion.sql` en `/migrations/`
2. Aplica la migración en local: `psql $DATABASE_URL -f migrations/NNN_descripcion.sql`
3. Dev de admin-console sincroniza el schema de Prisma:

```bash
# Desde admin-console/
npm run db:sync
# Equivale a: prisma db pull && prisma generate
```

Esto actualiza `schema.prisma` con los cambios nuevos y regenera el cliente TypeScript.

## Variables de entorno

Usar siempre un `.env` local apuntando a la DB local (nunca a producción):

```env
# DB local (Supabase local: supabase start)
DATABASE_URL="postgresql://postgres:postgres@localhost:54322/postgres"

# O Docker PostgreSQL
# DATABASE_URL="postgresql://postgres:localpass@localhost:5432/postgres"
```

Las credenciales de producción van **solo** en las variables de entorno de Railway/Vercel,
nunca en archivos `.env` locales.
