# Deployment

How `git push origin main` gets code to prod, and what you had to set up
once to make it work.

## Overview

```
git push → main
  │
  ├─→ .github/workflows/deploy.yml runs:
  │     1. test           (pytest)
  │     2. migrate        (alembic upgrade head against prod)
  │     3. deploy-vercel  (vercel CLI build + deploy --prebuilt --prod)
  │
  │   Jobs are sequential. Any red step stops the pipeline.
  │
  └─→ Railway auto-deploys the bot:
        With Railway's "Wait for CI" toggle on, Railway enters
        WAITING during the workflow and deploys the bot automatically
        once every job above passes. If any job fails, Railway
        SKIPS the deploy. No deploy hook needed.
```

PR workflow (`.github/workflows/ci.yml`) runs tests against every PR
targeting `main`, plus a smoke test that applies all raw SQL migrations
to an ephemeral postgres and runs `alembic upgrade head` against it.
No prod mutations.

## Database migrations

Source of truth: **Alembic** (`alembic/versions/`). Raw SQL in
`/migrations/` is the frozen historical archive that bootstraps fresh
local environments; no new raw SQL migrations should be added there.

To create a new migration:

```bash
# After editing a SQLAlchemy model in app/database/models.py:
DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres \
  alembic revision --autogenerate -m "add foo column to bar"

# Review the generated file in alembic/versions/
# Edit upgrade() / downgrade() if autogenerate missed anything (it can't
# detect CHECK constraints, renames, or complex data migrations — use
# op.execute("raw sql") for those).

# Apply locally to test:
DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres \
  alembic upgrade head

# Commit the file and push. The CI pipeline will apply it to prod.
```

Running migrations against a specific DB:

```bash
# Local
DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres alembic upgrade head

# Prod (dangerous — normally CI does this for you)
DATABASE_URL="$PROD_DATABASE_URL" alembic upgrade head

# Dry run (print SQL, don't apply)
DATABASE_URL="$PROD_DATABASE_URL" alembic upgrade head --sql
```

## One-time setup checklist

These had to be done once. Future pushes are automatic.

### 1. GitHub repository secrets

Settings → Secrets and variables → Actions → New repository secret.
Create four:

| Secret name         | Where to get it                                                                                                                                          |
|---------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `PROD_DATABASE_URL` | Supabase dashboard → Project Settings → Database → Connection string (URI). Use the `postgres://` form with the **service role** password. Verify it's the prod project, not a staging branch. |
| `VERCEL_TOKEN`      | Vercel dashboard → Account Settings → Tokens → Create Token. Scope: Full Account (needed for CLI deploys).                                               |
| `VERCEL_ORG_ID`     | Run `vercel link` locally inside `admin-console/`. Read from `admin-console/.vercel/project.json` → `orgId`.                                            |
| `VERCEL_PROJECT_ID` | Same file, `projectId` field.                                                                                                                            |

### 2. Disable Vercel's git auto-deploy

We want **the CI pipeline** to be the only thing that deploys Vercel.
Leaving git auto-deploy on would race with the migration step.

**Vercel** → project (`admin-console`) → Settings → Git →
**"Ignored Build Step"** → set to: `exit 0`

This tells Vercel to skip every git-triggered build. The Vercel CLI
(`vercel deploy --prebuilt --prod`) from GitHub Actions still works.

### 3. Enable Railway's "Wait for CI" toggle

Railway has native support for gating deploys behind CI status checks.
No deploy hook needed, and git auto-deploy stays on.

**Railway** → project → service (bot) → Settings → find
**"Wait for CI"** toggle → turn ON.

Requirements (we already meet them):
- Repo has a GitHub workflow with `on: push: branches: [main]`.
- Railway has the updated GitHub permissions (it'll prompt on first
  toggle; approve the permission request).

Behavior after enabling:
- Railway enters `WAITING` state during any GH Actions workflow on push.
- If any workflow job fails → deploy is `SKIPPED`, bot stays on the
  previous version.
- If all jobs succeed → Railway deploys automatically.

Docs: https://docs.railway.com/deployments/github-autodeploys

### 4. Catch up prod migrations (one time only)

Your prod DB is currently at raw SQL migrations 000–021. The new work
adds `022_add_order_item_notes.sql`, `023_add_product_search_metadata.sql`,
and an Alembic revision `596a0514423a` that cleans up NOT NULL / timestamp
drift. You need to apply all of them before the first CI-triggered push.

**Sequence** — run once from your local machine with prod credentials:

```bash
export DATABASE_URL="$PROD_DATABASE_URL"

# 4.1 Apply the raw SQL migrations that aren't in prod yet (022, 023).
#     These are the historical archive; Alembic doesn't manage them.
psql "$DATABASE_URL" -f migrations/022_add_order_item_notes.sql
psql "$DATABASE_URL" -f migrations/023_add_product_search_metadata.sql

# 4.2 Bootstrap Alembic at the baseline. This creates alembic_version
#     table in prod and marks the post-023 state as the starting point.
alembic stamp 44a057c1f6eb
alembic current   # should print: 44a057c1f6eb (head)

# 4.3 Apply the alignment migration (NOT NULL + staff_members TIMESTAMPTZ
#     + phone_number_id NOT NULL). Safe because server_default=NOW()
#     ensures no existing row is NULL.
alembic upgrade head
alembic current   # should print: 596a0514423a (head)

# 4.4 Apply the Biela tag seed + beverage descriptions (data, not schema).
#     Only needed if prod runs Biela's menu.
psql "$DATABASE_URL" -f scripts/biela/biela_product_metadata.sql
psql "$DATABASE_URL" -f scripts/biela/biela_beverage_descriptions_update.sql

unset DATABASE_URL
```

After this, prod matches `main`. The first push that lands on main will:
- Run tests
- Run `alembic upgrade head` → no-op (prod already at head)
- Deploy Vercel + Railway

### 5. Generate embeddings on prod (one time only, optional)

If you want the semantic search layer active in prod too, regenerate
product embeddings against prod's DATABASE_URL. Requires `OPENAI_API_KEY`
in your local `.env`:

```bash
python scripts/generate_product_metadata.py \
  --business-id 44488756-473b-46d2-a907-9f579e98ecfd \
  --embeddings-only
```

(Set `DATABASE_URL` to the prod URL inline or via `export` — the script
reads from env.)

### 6. Verify the setup

Make a trivial commit and push to a throwaway branch, open a PR. The
`CI` workflow runs `test` + `migrations-smoke` (which applies all raw
SQL migrations to an ephemeral postgres and runs `alembic upgrade head`
+ `alembic check` against it). If green, merge the PR to `main`. The
`Deploy` workflow runs `test → migrate → deploy-vercel` in sequence and
Railway deploys the bot once all three jobs pass.

## Troubleshooting

### `alembic upgrade head` fails in CI

Look at the GitHub Actions logs for the `migrate` job. Common causes:
- **Missing secret**: `PROD_DATABASE_URL` not set or pointing at the
  wrong project. Check the secret exists and has a value.
- **Network/firewall**: GitHub Actions runners hitting Supabase pooler
  — use the direct connection string (port 5432), not the pooler (6543).
- **Schema conflict**: a migration assumed a state that's different in
  prod vs your local. Reproduce against prod with `alembic upgrade head
  --sql` to see the SQL, fix the migration, push.

### CI migrates prod but Vercel or Railway deploy hook fails

The DB is migrated. Both hooks are simple POST requests that either
work or don't.
- **Vercel**: check the hook URL in repo secrets is valid by curling it
  manually. If it 404s, regenerate in Vercel dashboard.
- **Railway**: same.

Re-trigger a deploy by pushing an empty commit:
```bash
git commit --allow-empty -m "chore: retrigger deploy"
git push
```

### Autogenerate is clean

`alembic check` is enabled as a hard gate in the PR workflow. If you
change a SQLAlchemy model without adding a corresponding revision, CI
will fail.

To add a new migration:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres \
  alembic revision --autogenerate -m "describe your change"
```

Review the generated file (autogenerate is not perfect — check for
CHECK constraints, renames, or data migrations which it can't detect),
apply locally with `alembic upgrade head`, and commit the file.

The filters in `alembic/env.py` skip a few kinds of cosmetic drift that
can't be fixed cleanly via models alone:

- **Indexes** — prod uses inconsistent naming from the raw SQL
  migrations (`idx_staff_*` vs `idx_staff_members_*`, partial indexes
  with `WHERE` clauses, `_pkey` conventions). Alembic ignores index
  diffs entirely. If you add a new index, add it with raw SQL inside
  the migration's `upgrade()` via `op.execute(...)`.
- **Unique constraints** — same reason.
- **Unmodeled tables** — `processed_messages` exists in prod but is
  used via raw SQL (`app/services/message_deduplication.py`) and has
  no SQLAlchemy model. It's listed in `_UNMODELED_TABLES` in
  `alembic/env.py`. Add a proper model and remove it from the list
  when you want type-safe access.

The remaining drift detection catches the things that actually matter:
column adds/removes, column type changes, nullability, and table
adds/removes for modeled tables.

### Rolling back prod

Alembic has `alembic downgrade -1` which runs the previous revision's
`downgrade()`. Use with extreme care:
1. Check the `downgrade()` is actually written (many are TODOs).
2. Backup the DB via Supabase first.
3. `DATABASE_URL="$PROD_DATABASE_URL" alembic downgrade -1`
4. The bot may still have the newer code running — re-deploy the
   bot/admin console from a previous git commit if needed.

For destructive changes (dropped columns, renamed tables), prefer
**restoring from a Supabase backup** over `downgrade()`.
