# Database Migrations

This monorepo uses a shared PostgreSQL database with two ORMs:

| Component | ORM | Role |
|-----------|-----|------|
| `app/` (Python/Flask) | SQLAlchemy | **Write** - Schema owner, manages migrations |
| `admin-console/` (Next.js) | Prisma | **Read-only** - Introspects schema from database |

## Workflow

### 1. Make Schema Changes (SQLAlchemy)

Edit models in `app/database/models.py`:

```python
class Customer(Base):
    __tablename__ = 'customers'

    id = Column(Integer, primary_key=True)
    whatsapp_id = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    # Add new fields here
```

### 2. Apply to Database

From Python side, run migrations or use `create_tables()`:

```bash
python -c "from app.database.models import create_tables; create_tables()"
```

### 3. Sync Prisma Schema

After database changes, update Prisma schema:

```bash
cd admin-console
npx prisma db pull
npx prisma generate
```

## Important Notes

- **Never** modify `admin-console/prisma/schema.prisma` directly for schema changes
- Prisma schema is auto-generated from database introspection
- Both services share the same `DATABASE_URL` environment variable

---

## Role System

The application uses a two-level role system:

### System-Wide Role (`users.role`)

| Value | Who | Access |
|-------|-----|--------|
| `super_admin` | OmnIA team | Full access to all businesses |
| `NULL` | Business users | Access via `user_businesses` |

### Per-Business Role (`user_businesses.role`)

| Value | Who | Access |
|-------|-----|--------|
| `admin` | Business owner/admin | Can edit business settings |
| `staff` | Business employee | View-only access |

### Access Logic

1. **Super Admin**: Can see and edit ALL businesses
2. **Business Users**: Can only see businesses they're linked to via `user_businesses`
   - `admin` role: Can edit the business settings
   - `staff` role: View-only access

### Permission Helpers (Next.js)

Use the helpers in `admin-console/lib/permissions.ts`:

```typescript
import { isSuperAdmin, canAccessBusiness, canEditBusiness } from "@/lib/permissions"

// Check if user is OmnIA team
if (isSuperAdmin(session)) { ... }

// Check if user can view a business
if (canAccessBusiness(session, businessId)) { ... }

// Check if user can edit a business
if (canEditBusiness(session, businessId)) { ... }
```
