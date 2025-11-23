# Role System

This document describes the multi-tenant role system used across the WhatsApp Bot platform.

## Overview

The platform uses a **two-level role system**:

1. **System-wide role** (`users.role`) - Defines global platform access
2. **Per-business role** (`user_businesses.role`) - Defines access within specific businesses

## System-Wide Roles

Stored in `users.role` column:

| Role | Value | Description |
|------|-------|-------------|
| Super Admin | `"super_admin"` | OmnIA team members with full platform access |
| Business User | `null` | Regular users who access specific businesses |

### Super Admin
- Full access to all businesses
- Can create/delete businesses
- Can manage all users
- Can assign users to any business
- Visible "Users" menu in sidebar

### Business User
- Access only to assigned businesses
- Cannot create/delete businesses
- Cannot access user management
- Must have at least one business assignment

## Per-Business Roles

Stored in `user_businesses.role` column:

| Role | Value | Description |
|------|-------|-------------|
| Admin | `"admin"` | Business administrator |
| Staff | `"staff"` | Regular employee |

### Business Admin
- Can edit business settings
- Can invite staff to their business
- Can manage team members within their business
- Cannot create new businesses

### Business Staff
- Read-only access to business data
- Cannot modify business settings
- Cannot invite other users

## Database Schema

```
users
├── id (uuid)
├── email (string)
├── full_name (string)
├── role (string, nullable) ──► "super_admin" or NULL
├── is_active (boolean)
└── ...

user_businesses
├── id (uuid)
├── user_id (uuid) ──► FK to users
├── business_id (uuid) ──► FK to businesses
├── role (string) ──► "admin" or "staff"
└── ...
```

## Permission Checks

Permission helpers are located in `admin-console/lib/permissions.ts`:

```typescript
// Check if user is super admin
isSuperAdmin(session): boolean

// Check if user can access a specific business
canAccessBusiness(session, businessId): boolean

// Check if user can edit a business (super admin or business admin)
canEditBusiness(session, businessId): boolean

// Get list of business IDs user can access
getAccessibleBusinessIds(session): string[]
```

## Access Matrix

| Action | Super Admin | Business Admin | Business Staff |
|--------|-------------|----------------|----------------|
| View all businesses | Yes | No | No |
| Create business | Yes | No | No |
| Delete business | Yes | No | No |
| View assigned business | Yes | Yes | Yes |
| Edit business settings | Yes | Yes | No |
| View business team | Yes | Yes | Yes |
| Invite staff to business | Yes | Yes | No |
| Remove staff from business | Yes | Yes | No |
| Access user management | Yes | No | No |
| Create users | Yes | No | No |
| Delete users | Yes | No | No |

## Session Structure

When authenticated, the session includes business associations:

```typescript
interface Session {
  user: {
    id: string
    email: string
    name: string
    role: "super_admin" | null
    businesses: Array<{
      businessId: string
      businessName: string
      role: "admin" | "staff"
    }>
  }
}
```

## UI Behavior

### Create/Edit User Form
- When "Super Admin" role is selected, business assignments section is hidden
- When "Business User" role is selected, business assignments section appears
- Business users must have at least one business assignment

### Sidebar Navigation
- "Users" menu item only visible to super admins
- "Businesses" shows all businesses for super admins, only assigned businesses for others

### Business Settings
- Accessible to super admins and business admins
- Hidden/disabled for business staff

## Implementation Files

- **SQLAlchemy Model**: `app/database/models.py` - User model with `role` column
- **Prisma Schema**: `admin-console/prisma/schema.prisma` - Read-only, synced via introspection
- **Auth Config**: `admin-console/lib/auth.ts` - Session with business associations
- **Permissions**: `admin-console/lib/permissions.ts` - Permission helper functions
- **Type Definitions**: `admin-console/types/next-auth.d.ts` - Extended session types
