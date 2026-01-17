"""
Database service for managing business operations in multi-tenant system.
Handles businesses, WhatsApp numbers, users, and their relationships.
"""

import logging
from typing import Optional, Dict, List
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_
import uuid
from .models import Business, WhatsappNumber, User, UserBusiness, get_db_session

class BusinessService:
    """Service for managing business operations."""

    def __init__(self):
        """Initialize the business service."""
        logging.info("BusinessService initialized")

    # ========================================================================
    # BUSINESS OPERATIONS
    # ========================================================================

    def get_business(self, business_id: str) -> Optional[Dict]:
        """
        Get business by ID.

        Args:
            business_id: Business UUID

        Returns:
            Business information as dictionary, or None if not found
        """
        try:
            session: Session = get_db_session()

            business = session.query(Business)\
                .filter(Business.id == uuid.UUID(business_id))\
                .first()

            session.close()

            if business:
                logging.debug(f"Retrieved business: {business.name}")
                return business.to_dict()
            else:
                logging.debug(f"No business found with ID {business_id}")
                return None

        except Exception as e:
            logging.error(f"Error getting business {business_id}: {e}")
            return None

    def get_business_by_name(self, name: str) -> Optional[Dict]:
        """Get business by name."""
        try:
            session: Session = get_db_session()

            business = session.query(Business)\
                .filter(Business.name == name)\
                .first()

            session.close()

            if business:
                return business.to_dict()
            return None

        except Exception as e:
            logging.error(f"Error getting business by name: {e}")
            return None

    def create_business(self, name: str, business_type: str = "barberia",
                       settings: Dict = None) -> Optional[Dict]:
        """
        Create a new business.

        Args:
            name: Business name
            business_type: Type of business (barberia, salon, etc.)
            settings: Business settings (JSONB)

        Returns:
            Created business information as dictionary, or None if failed
        """
        try:
            session: Session = get_db_session()

            business = Business(
                name=name,
                business_type=business_type,
                settings=settings or {},
                is_active=True
            )

            session.add(business)
            session.commit()

            business_dict = business.to_dict()
            session.close()

            logging.info(f"Created business: {name} (ID: {business_dict['id']})")
            return business_dict

        except IntegrityError as e:
            logging.error(f"Business integrity error: {e}")
            return None
        except Exception as e:
            logging.error(f"Error creating business: {e}")
            return None

    def update_business(self, business_id: str, name: str = None,
                       business_type: str = None, settings: Dict = None,
                       is_active: bool = None) -> Optional[Dict]:
        """Update existing business information."""
        try:
            session: Session = get_db_session()

            business = session.query(Business)\
                .filter(Business.id == uuid.UUID(business_id))\
                .first()

            if not business:
                session.close()
                logging.warning(f"No business found to update with ID {business_id}")
                return None

            # Update fields if provided
            if name is not None:
                business.name = name
            if business_type is not None:
                business.business_type = business_type
            if settings is not None:
                business.settings = settings
            if is_active is not None:
                business.is_active = is_active

            session.commit()
            business_dict = business.to_dict()
            session.close()

            logging.info(f"Updated business: {business_dict['name']}")
            return business_dict

        except Exception as e:
            logging.error(f"Error updating business: {e}")
            return None

    def get_all_businesses(self, active_only: bool = True) -> List[Dict]:
        """Get all businesses."""
        try:
            session: Session = get_db_session()

            query = session.query(Business)
            if active_only:
                query = query.filter(Business.is_active == True)

            businesses = query.all()
            business_list = [b.to_dict() for b in businesses]

            session.close()

            logging.debug(f"Retrieved {len(business_list)} businesses")
            return business_list

        except Exception as e:
            logging.error(f"Error getting all businesses: {e}")
            return []

    # ========================================================================
    # WHATSAPP NUMBER OPERATIONS
    # ========================================================================

    def get_whatsapp_number_by_phone_number_id(self, phone_number_id: str) -> Optional[Dict]:
        """
        Get WhatsApp number by Meta's phone_number_id.
        This is the key lookup for routing incoming webhooks.

        Args:
            phone_number_id: Meta's phone number ID from webhook

        Returns:
            WhatsApp number information with business_id, or None if not found
        """
        try:
            session: Session = get_db_session()

            whatsapp_number = session.query(WhatsappNumber)\
                .filter(and_(
                    WhatsappNumber.phone_number_id == phone_number_id,
                    WhatsappNumber.is_active == True
                ))\
                .first()

            session.close()

            if whatsapp_number:
                logging.debug(f"Found WhatsApp number for business_id: {whatsapp_number.business_id}")
                return whatsapp_number.to_dict()
            else:
                logging.warning(f"No active WhatsApp number found for phone_number_id: {phone_number_id}")
                return None

        except Exception as e:
            logging.error(f"Error getting WhatsApp number: {e}")
            return None

    def create_whatsapp_number(self, business_id: str, phone_number_id: str,
                              phone_number: str, display_name: str = None) -> Optional[Dict]:
        """
        Create a new WhatsApp number for a business.
        Note: Credentials (access_token, app_id, etc.) are shared and stored in .env.

        Args:
            business_id: Business UUID
            phone_number_id: Meta's phone number ID (unique per business)
            phone_number: Display phone number (e.g., +15556738752)
            display_name: Optional friendly name (e.g., "Main Line", "Support Line")

        Returns:
            Created WhatsApp number information, or None if failed
        """
        try:
            session: Session = get_db_session()

            whatsapp_number = WhatsappNumber(
                business_id=uuid.UUID(business_id),
                phone_number_id=phone_number_id,
                phone_number=phone_number,
                display_name=display_name,
                is_active=True
            )

            session.add(whatsapp_number)
            session.commit()

            whatsapp_dict = whatsapp_number.to_dict()
            session.close()

            logging.info(f"Created WhatsApp number {phone_number} (ID: {phone_number_id}) for business {business_id}")
            return whatsapp_dict

        except IntegrityError as e:
            logging.error(f"WhatsApp number already exists: {e}")
            return None
        except Exception as e:
            logging.error(f"Error creating WhatsApp number: {e}")
            return None

    def get_business_whatsapp_numbers(self, business_id: str) -> List[Dict]:
        """Get all WhatsApp numbers for a business."""
        try:
            session: Session = get_db_session()

            numbers = session.query(WhatsappNumber)\
                .filter(WhatsappNumber.business_id == uuid.UUID(business_id))\
                .all()

            number_list = [n.to_dict() for n in numbers]
            session.close()

            logging.debug(f"Retrieved {len(number_list)} WhatsApp numbers for business {business_id}")
            return number_list

        except Exception as e:
            logging.error(f"Error getting WhatsApp numbers: {e}")
            return []

    def update_whatsapp_number(self, whatsapp_number_id: str,
                              phone_number: str = None, display_name: str = None,
                              is_active: bool = None) -> Optional[Dict]:
        """Update WhatsApp number (e.g., change display number, display name, activate/deactivate)."""
        try:
            session: Session = get_db_session()

            whatsapp_number = session.query(WhatsappNumber)\
                .filter(WhatsappNumber.id == uuid.UUID(whatsapp_number_id))\
                .first()

            if not whatsapp_number:
                session.close()
                return None

            if phone_number is not None:
                whatsapp_number.phone_number = phone_number
            if display_name is not None:
                whatsapp_number.display_name = display_name
            if is_active is not None:
                whatsapp_number.is_active = is_active

            session.commit()
            whatsapp_dict = whatsapp_number.to_dict()
            session.close()

            logging.info(f"Updated WhatsApp number {whatsapp_number_id}")
            return whatsapp_dict

        except Exception as e:
            logging.error(f"Error updating WhatsApp number: {e}")
            return None

    # ========================================================================
    # USER OPERATIONS
    # ========================================================================

    def create_user(self, email: str, password_hash: str,
                   full_name: str = None) -> Optional[Dict]:
        """Create a new user."""
        try:
            session: Session = get_db_session()

            user = User(
                email=email,
                password_hash=password_hash,
                full_name=full_name,
                is_active=True
            )

            session.add(user)
            session.commit()

            user_dict = user.to_dict()
            session.close()

            logging.info(f"Created user: {email}")
            return user_dict

        except IntegrityError as e:
            logging.error(f"User already exists: {e}")
            return None
        except Exception as e:
            logging.error(f"Error creating user: {e}")
            return None

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Get user by email."""
        try:
            session: Session = get_db_session()

            user = session.query(User)\
                .filter(User.email == email)\
                .first()

            session.close()

            if user:
                return user.to_dict()
            return None

        except Exception as e:
            logging.error(f"Error getting user: {e}")
            return None

    # ========================================================================
    # USER-BUSINESS RELATIONSHIP OPERATIONS
    # ========================================================================

    def add_user_to_business(self, user_id: str, business_id: str,
                           role: str = "staff") -> Optional[Dict]:
        """Add a user to a business with a specific role."""
        try:
            session: Session = get_db_session()

            user_business = UserBusiness(
                user_id=uuid.UUID(user_id),
                business_id=uuid.UUID(business_id),
                role=role
            )

            session.add(user_business)
            session.commit()

            ub_dict = user_business.to_dict()
            session.close()

            logging.info(f"Added user {user_id} to business {business_id} as {role}")
            return ub_dict

        except IntegrityError as e:
            logging.warning(f"User-business relationship already exists: {e}")
            return None
        except Exception as e:
            logging.error(f"Error adding user to business: {e}")
            return None

    def get_user_businesses(self, user_id: str) -> List[Dict]:
        """Get all businesses a user has access to."""
        try:
            session: Session = get_db_session()

            user_businesses = session.query(Business, UserBusiness)\
                .join(UserBusiness, Business.id == UserBusiness.business_id)\
                .filter(UserBusiness.user_id == uuid.UUID(user_id))\
                .all()

            result = []
            for business, ub in user_businesses:
                business_dict = business.to_dict()
                business_dict['role'] = ub.role
                result.append(business_dict)

            session.close()

            logging.debug(f"User {user_id} has access to {len(result)} businesses")
            return result

        except Exception as e:
            logging.error(f"Error getting user businesses: {e}")
            return []

    def get_business_users(self, business_id: str) -> List[Dict]:
        """Get all users who have access to a business."""
        try:
            session: Session = get_db_session()

            business_users = session.query(User, UserBusiness)\
                .join(UserBusiness, User.id == UserBusiness.user_id)\
                .filter(UserBusiness.business_id == uuid.UUID(business_id))\
                .all()

            result = []
            for user, ub in business_users:
                user_dict = user.to_dict()
                user_dict['role'] = ub.role
                result.append(user_dict)

            session.close()

            logging.debug(f"Business {business_id} has {len(result)} users")
            return result

        except Exception as e:
            logging.error(f"Error getting business users: {e}")
            return []

    # ========================================================================
    # UTILITY OPERATIONS
    # ========================================================================

    def get_business_context(self, phone_number_id: str) -> Optional[Dict]:
        """
        Get complete business context from phone_number_id.
        This is the main method for routing incoming webhooks.

        Args:
            phone_number_id: Meta's phone number ID from webhook

        Returns:
            Dictionary with business, whatsapp_number, and access info
        """
        try:
            # Get WhatsApp number
            whatsapp_number = self.get_whatsapp_number_by_phone_number_id(phone_number_id)

            if not whatsapp_number:
                logging.error(f"No WhatsApp number found for {phone_number_id}")
                return None

            # Get business
            business = self.get_business(whatsapp_number['business_id'])

            if not business:
                logging.error(f"No business found for {whatsapp_number['business_id']}")
                return None

            context = {
                'business': business,
                'whatsapp_number': whatsapp_number,
                'business_id': business['id'],
                'whatsapp_number_id': whatsapp_number['id'],
                'phone_number_id': phone_number_id  # Meta's phone number ID for API calls
            }

            logging.info(f"[CONTEXT] Loaded context for business: {business['name']}")
            return context

        except Exception as e:
            logging.error(f"Error getting business context: {e}")
            return None


# Global instance
business_service = BusinessService()
