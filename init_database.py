#!/usr/bin/env python3
"""
Database initialization script for WhatsApp Bot.
Creates the database tables and verifies connection.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add app directory to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.database.models import create_tables, get_db_session, DATABASE_URL
from app.database.conversation_service import conversation_service
from app.database.customer_service import customer_service

def main():
    """Initialize the database."""
    print("🗄️  WhatsApp Bot Database Initialization")
    print("=" * 50)

    # Check database URL
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL environment variable is not set!")
        print("   Please add your Supabase PostgreSQL connection string to .env file:")
        print("   DATABASE_URL=postgresql://postgres:[password]@db.[project-ref].supabase.co:5432/postgres")
        sys.exit(1)

    print(f"🔗 Database URL: {DATABASE_URL[:50]}...")

    try:
        # Create tables
        print("\n📋 Creating database tables...")
        create_tables()
        print("✅ Tables created successfully!")

        # Test connection
        print("\n🔌 Testing database connection...")
        session = get_db_session()
        session.close()
        print("✅ Database connection successful!")

        # Test conversation service
        print("\n🤖 Testing conversation service...")
        test_wa_id = "test_init_001"

        # Store a test message
        success = conversation_service.store_conversation_message(
            test_wa_id,
            "Test message for database initialization",
            "user"
        )

        if success:
            print("✅ Message storage test successful!")

            # Retrieve the message
            history = conversation_service.get_conversation_history(test_wa_id)
            if history and len(history) > 0:
                print("✅ Message retrieval test successful!")

                # Clean up test data
                conversation_service.clear_conversation_history(test_wa_id)
                print("✅ Test data cleaned up!")
            else:
                print("❌ Message retrieval test failed!")
        else:
            print("❌ Message storage test failed!")

        print("\n🎉 Database initialization completed successfully!")
        print("   Your WhatsApp bot is ready to store conversation history in PostgreSQL.")

    except Exception as e:
        print(f"\n❌ Database initialization failed: {e}")
        print("\nTroubleshooting:")
        print("1. Check that your DATABASE_URL is correct")
        print("2. Ensure your Supabase project is active")
        print("3. Verify network connectivity to Supabase")
        print("4. Check that the database user has CREATE TABLE permissions")
        sys.exit(1)

if __name__ == "__main__":
    main()