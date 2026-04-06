#!/usr/bin/env bash
# =============================================================================
# setup_local.sh — Configura el ambiente de desarrollo local
# Requiere: Supabase CLI (https://supabase.com/docs/guides/cli)
# =============================================================================
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DB_URL="postgresql://postgres:postgres@localhost:54322/postgres"

echo ""
echo "🚀 Configurando ambiente local..."
echo ""

# ── 1. Verificar Supabase CLI ──────────────────────────────────────────────
if ! command -v supabase &>/dev/null; then
  echo "❌ Supabase CLI no instalado."
  echo "   Instalar con: brew install supabase/tap/supabase"
  echo "   Docs: https://supabase.com/docs/guides/cli/getting-started"
  exit 1
fi

# ── 2. Inicializar Supabase si no existe ────────────────────────────────────
if [ ! -f "$REPO_ROOT/supabase/config.toml" ]; then
  echo "📁 Inicializando Supabase local..."
  cd "$REPO_ROOT"
  supabase init
fi

# ── 3. Arrancar Supabase local ──────────────────────────────────────────────
echo "🐘 Arrancando Supabase local (puede tardar la primera vez)..."
cd "$REPO_ROOT"
supabase start

echo ""
echo "✅ Supabase local corriendo."
echo "   DB URL: $LOCAL_DB_URL"
echo "   Studio:  http://localhost:54323"
echo ""

# ── 4. Aplicar migraciones ──────────────────────────────────────────────────
if ! command -v psql &>/dev/null; then
  echo "⚠️  psql no encontrado. Instalar con: brew install postgresql"
  echo "   Las migraciones NO fueron aplicadas — correrlas manualmente:"
  for f in "$REPO_ROOT"/migrations/[0-9]*.sql; do
    echo "   psql $LOCAL_DB_URL -f $f"
  done
  echo ""
else
  echo "📦 Aplicando migraciones SQL..."
  for f in "$REPO_ROOT"/migrations/[0-9]*.sql; do
    name=$(basename "$f")
    echo "   → $name"
    psql "$LOCAL_DB_URL" -f "$f" -v ON_ERROR_STOP=1 -q
  done
  echo "✅ Migraciones aplicadas."
  echo ""
fi

# ── 5. Crear archivos .env si no existen ────────────────────────────────────
if [ ! -f "$REPO_ROOT/.env" ]; then
  cp "$REPO_ROOT/.env.local.example" "$REPO_ROOT/.env"
  echo "📝 Creado .env para el bot Python. Completar con tus API keys."
fi

if [ ! -f "$REPO_ROOT/admin-console/.env" ]; then
  cp "$REPO_ROOT/admin-console/.env.local.example" "$REPO_ROOT/admin-console/.env"
  echo "📝 Creado admin-console/.env. Ajustar si es necesario."
fi

# ── 6. Regenerar cliente Prisma ─────────────────────────────────────────────
if command -v npm &>/dev/null && [ -f "$REPO_ROOT/admin-console/package.json" ]; then
  echo ""
  echo "🔄 Sincronizando schema Prisma con la DB local..."
  cd "$REPO_ROOT/admin-console"
  # prisma db pull introspecta la DB y actualiza schema.prisma
  npx prisma db pull
  npx prisma generate
  echo "✅ Cliente Prisma generado."
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✅ Ambiente local listo"
echo ""
echo "  Bot Python:    cd $REPO_ROOT && python run.py"
echo "  Admin console: cd $REPO_ROOT/admin-console && npm run dev"
echo "  Supabase Studio: http://localhost:54323"
echo "════════════════════════════════════════════════════════════"
echo ""
