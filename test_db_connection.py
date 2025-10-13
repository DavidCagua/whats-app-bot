"""
Quick test script to verify database connection works.
Run this after updating DATABASE_URL to use Direct Connection.
"""

from app.database.models import engine, Business
from sqlalchemy import text
import sys

def test_connection():
    """Test database connection and basic query."""
    try:
        print("Testing database connection...")
        print(f"Connecting to database...")

        # Test 1: Basic connection
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            print("✓ Basic connection successful")

        # Test 2: Check if we can query businesses table
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM businesses"))
            count = result.scalar()
            print(f"✓ Can query businesses table (found {count} businesses)")

        # Test 3: Check connection pool info
        print(f"\nConnection Pool Info:")
        print(f"  - Pool size: {engine.pool.size()}")
        print(f"  - Checked out connections: {engine.pool.checkedout()}")
        print(f"  - Overflow: {engine.pool.overflow()}")

        print("\n✅ All database connection tests passed!")
        print("\nYou can now:")
        print("  1. Update your .env with the Direct Connection URL")
        print("  2. Update Render environment variables with the same URL")
        print("  3. Redeploy to Render")

        return True

    except Exception as e:
        print(f"\n❌ Database connection failed!")
        print(f"Error: {str(e)}")
        print("\nTroubleshooting:")
        print("  1. Make sure DATABASE_URL is set in your .env file")
        print("  2. Use the DIRECT CONNECTION string from Supabase")
        print("  3. Find it in: Supabase Dashboard > Project Settings > Database > Connection String")
        print("  4. Select 'Direct Connection' (not Transaction Pooler)")
        print("  5. Copy the entire connection string including password")
        return False

if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
