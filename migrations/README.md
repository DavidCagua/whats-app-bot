# Database Migrations

This directory contains SQL migration files for the WhatsApp Bot database schema.

## Migration Files

### 001_multi_tenant_schema.sql
Transforms the single-business bot into a multi-tenant system supporting:
- Multiple businesses
- Multiple WhatsApp numbers per business
- User management and access control
- Business-specific configurations

### Running Migrations

#### Option 1: Using Supabase Dashboard
1. Go to your Supabase project dashboard
2. Navigate to SQL Editor
3. Copy and paste the contents of the migration file
4. Execute the SQL

#### Option 2: Using psql CLI
```bash
psql postgresql://postgres:[password]@db.[project-ref].supabase.co:5432/postgres \
  -f migrations/001_multi_tenant_schema.sql
```

#### Option 3: Using Python script
```bash
python run_migration.py 001
```

### Rollback

To rollback a migration:
```bash
psql postgresql://postgres:[password]@db.[project-ref].supabase.co:5432/postgres \
  -f migrations/001_multi_tenant_schema_rollback.sql
```

## Migration History

| Version | Description | Date | Status |
|---------|-------------|------|--------|
| 001 | Multi-tenant schema | 2025-10-11 | Pending |

## Schema Overview

### New Tables

#### `businesses`
Core table for organizations/barbershops.
- Stores business name, type, settings (JSONB)
- Settings include: services, prices, hours, barbers, AI personality

#### `whatsapp_numbers`
WhatsApp Business API phone numbers.
- Links to a business
- Stores Meta credentials (phone_number_id, access_token)
- Each number can receive messages

#### `users`
System users who can manage businesses.
- Email, password hash, full name
- Can access multiple businesses

#### `user_businesses`
Many-to-many relationship between users and businesses.
- Stores role (owner, admin, staff)

### Updated Tables

#### `customers`
- Added `business_id` (FK to businesses)
- Customers now belong to specific businesses

#### `conversations`
- Added `business_id` (FK to businesses)
- Added `whatsapp_number_id` (FK to whatsapp_numbers)
- Conversations are scoped to business context

## Default Business

Migration 001 creates a default business with ID:
```
00000000-0000-0000-0000-000000000001
```

All existing customers and conversations are migrated to this default business.

## Important Notes

1. **Backup First**: Always backup your database before running migrations
2. **Test in Staging**: Run migrations in a staging environment first
3. **Monitor Indexes**: New indexes improve query performance
4. **Encryption**: Access tokens should be encrypted at the application layer
5. **Triggers**: Updated_at columns are automatically maintained via triggers

## Next Steps After Migration

1. Run migration 001
2. Verify default business was created
3. Add WhatsApp number for default business
4. Update application code to use new models
5. Test end-to-end flow
