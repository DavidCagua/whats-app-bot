#!/usr/bin/env python3
"""
Script to verify WhatsApp bot configuration and database setup.
Run this to check if everything is configured correctly.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def check_env_vars():
    """Check if all required environment variables are set."""
    print("=" * 60)
    print("CHECKING ENVIRONMENT VARIABLES")
    print("=" * 60)
    
    required_vars = [
        "ACCESS_TOKEN",
        "APP_ID",
        "APP_SECRET",
        "PHONE_NUMBER_ID",
        "VERSION",
        "VERIFY_TOKEN",
        "OPENAI_API_KEY",
        "DATABASE_URL"
    ]
    
    missing = []
    for var in required_vars:
        value = os.getenv(var)
        if value:
            # Mask sensitive values
            if "TOKEN" in var or "SECRET" in var or "KEY" in var or "PASSWORD" in var:
                masked = value[:10] + "..." + value[-5:] if len(value) > 15 else "***"
                print(f"✅ {var}: {masked}")
            else:
                print(f"✅ {var}: {value}")
        else:
            print(f"❌ {var}: NOT SET")
            missing.append(var)
    
    if missing:
        print(f"\n⚠️  Missing environment variables: {', '.join(missing)}")
        return False
    else:
        print("\n✅ All required environment variables are set!")
        return True

def check_database():
    """Check database connection and phone_number_id setup."""
    print("\n" + "=" * 60)
    print("CHECKING DATABASE CONNECTION")
    print("=" * 60)
    
    try:
        from app.database.models import get_db_session, WhatsappNumber, Business
        from sqlalchemy.orm import Session
        
        session: Session = get_db_session()
        print("✅ Database connection successful")
        
        # Check if phone_number_id exists
        phone_number_id = os.getenv("PHONE_NUMBER_ID")
        if phone_number_id:
            print(f"\nChecking for phone_number_id: {phone_number_id}")
            whatsapp_number = session.query(WhatsappNumber)\
                .filter(WhatsappNumber.phone_number_id == phone_number_id)\
                .first()
            
            if whatsapp_number:
                print(f"✅ Phone number found in database")
                print(f"   - ID: {whatsapp_number.id}")
                print(f"   - Phone Number: {whatsapp_number.phone_number}")
                print(f"   - Display Name: {whatsapp_number.display_name or 'N/A'}")
                print(f"   - Active: {whatsapp_number.is_active}")
                print(f"   - Business ID: {whatsapp_number.business_id}")
                
                # Check business
                business = session.query(Business)\
                    .filter(Business.id == whatsapp_number.business_id)\
                    .first()
                
                if business:
                    print(f"✅ Business found: {business.name}")
                    print(f"   - Active: {business.is_active}")
                else:
                    print(f"❌ Business not found for ID: {whatsapp_number.business_id}")
            else:
                print(f"❌ Phone number ID '{phone_number_id}' NOT found in database")
                print(f"\n⚠️  You need to add this phone_number_id to the database!")
                print(f"   Use the admin console or run:")
                print(f"   python3 -c \"from app.database.business_service import business_service; print(business_service.create_whatsapp_number('YOUR_BUSINESS_ID', '{phone_number_id}', '+573177000722'))\"")
        
        # List all WhatsApp numbers
        all_numbers = session.query(WhatsappNumber).all()
        print(f"\n📋 All WhatsApp numbers in database ({len(all_numbers)}):")
        for num in all_numbers:
            status = "✅ Active" if num.is_active else "❌ Inactive"
            print(f"   - {num.phone_number_id} ({num.phone_number}) - {status}")
        
        session.close()
        return True
        
    except Exception as e:
        print(f"❌ Database error: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_openai():
    """Check OpenAI API key."""
    print("\n" + "=" * 60)
    print("CHECKING OPENAI API")
    print("=" * 60)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        print(f"✅ OPENAI_API_KEY is set")
        if api_key.startswith("sk-"):
            print("✅ Key format looks correct (starts with 'sk-')")
        else:
            print("⚠️  Key format might be incorrect (should start with 'sk-')")
        return True
    else:
        print("❌ OPENAI_API_KEY is not set")
        return False

def main():
    """Run all checks."""
    print("\n🔍 WhatsApp Bot Configuration Checker\n")
    
    env_ok = check_env_vars()
    db_ok = check_database()
    openai_ok = check_openai()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    if env_ok and db_ok and openai_ok:
        print("✅ All checks passed! Your configuration looks good.")
        print("\n💡 Next steps:")
        print("   1. Make sure your webhook is configured in Meta Dashboard")
        print("   2. Start the app: FLASK_DEBUG=True python3 run.py")
        print("   3. Send a test message to your WhatsApp number")
        return 0
    else:
        print("❌ Some checks failed. Please fix the issues above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
