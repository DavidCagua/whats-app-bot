"""
Database Migration Runner

Usage:
    python run_migration.py <migration_number> [--rollback]

Examples:
    python run_migration.py 001
    python run_migration.py 001 --rollback
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def run_migration(migration_number: str, rollback: bool = False):
    """Run a database migration or rollback."""

    # Get database URL from environment
    database_url = os.getenv('DATABASE_URL')

    if not database_url:
        print("❌ Error: DATABASE_URL environment variable not set")
        print("Please set DATABASE_URL in your .env file")
        return False

    # Determine migration file
    suffix = "_rollback" if rollback else ""
    migration_file = f"migrations/{migration_number}_multi_tenant_schema{suffix}.sql"

    if not os.path.exists(migration_file):
        print(f"❌ Error: Migration file not found: {migration_file}")
        return False

    # Read migration SQL
    with open(migration_file, 'r') as f:
        migration_sql = f.read()

    # Connect to database
    try:
        print(f"📡 Connecting to database...")
        conn = psycopg2.connect(database_url)
        conn.autocommit = False  # Use transactions
        cursor = conn.cursor()

        action = "Rolling back" if rollback else "Running"
        print(f"🚀 {action} migration {migration_number}...")
        print(f"📄 File: {migration_file}")

        # Execute migration
        cursor.execute(migration_sql)

        # Commit transaction
        conn.commit()

        action_past = "rolled back" if rollback else "applied"
        print(f"✅ Migration {migration_number} {action_past} successfully!")

        # Show some stats
        if not rollback:
            cursor.execute("SELECT COUNT(*) FROM businesses")
            business_count = cursor.fetchone()[0]
            print(f"📊 Businesses in database: {business_count}")

            cursor.execute("SELECT COUNT(*) FROM customers")
            customer_count = cursor.fetchone()[0]
            print(f"📊 Customers in database: {customer_count}")

            cursor.execute("SELECT COUNT(*) FROM conversations")
            conversation_count = cursor.fetchone()[0]
            print(f"📊 Conversations in database: {conversation_count}")

        cursor.close()
        conn.close()

        return True

    except psycopg2.Error as e:
        print(f"❌ Database error: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False


def show_migration_status():
    """Show current migration status."""
    database_url = os.getenv('DATABASE_URL')

    if not database_url:
        print("❌ Error: DATABASE_URL environment variable not set")
        return

    try:
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()

        print("\n📊 Current Database Status:")
        print("=" * 60)

        # Check if businesses table exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'businesses'
            )
        """)
        businesses_exists = cursor.fetchone()[0]

        if businesses_exists:
            print("✅ Multi-tenant schema: APPLIED")

            cursor.execute("SELECT COUNT(*) FROM businesses")
            business_count = cursor.fetchone()[0]
            print(f"   - Businesses: {business_count}")

            cursor.execute("SELECT COUNT(*) FROM whatsapp_numbers")
            number_count = cursor.fetchone()[0]
            print(f"   - WhatsApp Numbers: {number_count}")

            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = cursor.fetchone()[0]
            print(f"   - Users: {user_count}")

        else:
            print("⚠️  Multi-tenant schema: NOT APPLIED")
            print("   Run: python run_migration.py 001")

        print("=" * 60)

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"❌ Error checking migration status: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_migration.py <migration_number> [--rollback]")
        print("\nAvailable migrations:")
        print("  001 - Multi-tenant schema")
        print("\nOptions:")
        print("  --rollback - Rollback the migration")
        print("  --status   - Show migration status")
        print("\nExamples:")
        print("  python run_migration.py 001")
        print("  python run_migration.py 001 --rollback")
        print("  python run_migration.py --status")
        sys.exit(1)

    # Check for status command
    if sys.argv[1] == "--status":
        show_migration_status()
        sys.exit(0)

    migration_number = sys.argv[1]
    rollback = "--rollback" in sys.argv

    # Confirm rollback
    if rollback:
        print("⚠️  WARNING: This will rollback the migration and may delete data!")
        response = input("Are you sure you want to continue? (yes/no): ")
        if response.lower() != "yes":
            print("❌ Rollback cancelled")
            sys.exit(0)

    success = run_migration(migration_number, rollback)

    if success:
        print("\n✅ Done!")
        if not rollback:
            print("\n📝 Next steps:")
            print("1. Verify migration: python run_migration.py --status")
            print("2. Add WhatsApp number for your business")
            print("3. Update application code to use new models")
    else:
        print("\n❌ Migration failed!")
        sys.exit(1)
