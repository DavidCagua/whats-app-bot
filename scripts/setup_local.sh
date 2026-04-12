#!/usr/bin/env bash
# =============================================================================
# setup_local.sh — Configura el ambiente de desarrollo local
# Requiere: Supabase CLI (https://supabase.com/docs/guides/cli)
# =============================================================================
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DB_URL="postgresql://postgres:postgres@localhost:54322/postgres"

# Schema = SQL migrations only (same as prod). 000 = greenfield tables legacy 001 assumed.
# Never run *_rollback.sql here.
# 021 is omitted: it repeats RLS + policies for conversations/customers/processed_messages
# already applied in 020; running 021 on a fresh DB fails with duplicate policy errors.
MIGRATION_NUMBERS=(
  000 001 002 003 004 005 006 007 008 009 010 011 012 013 014 015 016 017 018 019 020 022 023
)

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

# ── 4. Aplicar migraciones SQL (000–020; ver comentario sobre 021 arriba) ─────────
if ! command -v psql &>/dev/null; then
  echo "⚠️  psql no encontrado. Instalar con: brew install postgresql"
  echo "   Las migraciones NO fueron aplicadas — correrlas manualmente:"
  for n in "${MIGRATION_NUMBERS[@]}"; do
    for f in "$REPO_ROOT/migrations/${n}"_*.sql; do
      [[ -f "$f" ]] || continue
      [[ "$f" == *_rollback.sql ]] && continue
      echo "   psql $LOCAL_DB_URL -f $f"
    done
  done
  echo ""
else
  echo "📦 Aplicando migraciones SQL (000–020, sin rollbacks)..."
  for n in "${MIGRATION_NUMBERS[@]}"; do
    for f in "$REPO_ROOT/migrations/${n}"_*.sql; do
      [[ -f "$f" ]] || continue
      [[ "$f" == *_rollback.sql ]] && continue
      name=$(basename "$f")
      echo "   → $name"
      psql "$LOCAL_DB_URL" -f "$f" -v ON_ERROR_STOP=1 -q
    done
  done
  echo "✅ Migraciones aplicadas."
  echo ""

  echo "🍔 Biela (negocio + Twilio +14155238886, menú)..."
  psql "$LOCAL_DB_URL" -f "$REPO_ROOT/scripts/biela/ensure_business_and_whatsapp.sql" -v ON_ERROR_STOP=1 -q
  psql "$LOCAL_DB_URL" -f "$REPO_ROOT/scripts/seed_biela_menu.sql" -v ON_ERROR_STOP=1 -q
  psql "$LOCAL_DB_URL" -f "$REPO_ROOT/scripts/biela/biela_product_metadata.sql" -v ON_ERROR_STOP=1 -q
  echo "✅ Biela lista (menú + tags de búsqueda)."
  echo ""

  # Generate embeddings. The Python script loads OPENAI_API_KEY from .env via
  # python-dotenv, so we don't gate on shell-env here. If the key is missing,
  # the script logs an error and exits 1; we downgrade that to a warning.
  if command -v python3 &>/dev/null; then
    echo "🧠 Generando embeddings de productos (requiere OPENAI_API_KEY en .env)..."
    cd "$REPO_ROOT"
    if python3 scripts/generate_product_metadata.py \
        --business-id 44488756-473b-46d2-a907-9f579e98ecfd \
        --embeddings-only; then
      echo "✅ Embeddings generados."
    else
      echo "   ⚠️  Embeddings no generados (¿OPENAI_API_KEY en .env?)."
      echo "    Reintentar: python scripts/generate_product_metadata.py --business-id 44488756-473b-46d2-a907-9f579e98ecfd --embeddings-only"
    fi
    echo ""
  fi
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
  DATABASE_URL="$LOCAL_DB_URL" npx prisma db pull
  DATABASE_URL="$LOCAL_DB_URL" npx prisma generate
  echo "✅ Cliente Prisma generado."
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✅ Ambiente local listo"
echo ""
echo "  Bot Python:    cd $REPO_ROOT && python run.py"
echo "  Admin console: cd $REPO_ROOT/admin-console && npm run dev"
echo "  Supabase Studio: http://localhost:54323"
echo "  Biela: business + Twilio sandbox + menú (scripts/biela/)"
echo "════════════════════════════════════════════════════════════"
echo ""
