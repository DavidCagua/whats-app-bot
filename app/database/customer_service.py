"""
Database service for managing customer information.
Handles customer data collected during appointment scheduling.

Note: Customer data is business-agnostic (a person is a person).
Business relationships are tracked through conversations, not customers.
"""

import logging
from typing import Optional, Dict, List
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .models import Customer, get_db_session

class CustomerService:
    """Service for managing customer information in PostgreSQL."""

    def __init__(self):
        """Initialize the customer service."""
        logging.info("CustomerService initialized")

    def get_customer(self, whatsapp_id: str) -> Optional[Dict]:
        """
        Get customer information by WhatsApp ID.

        Args:
            whatsapp_id: WhatsApp ID

        Returns:
            Customer information as dictionary, or None if not found
        """
        try:
            session: Session = get_db_session()

            customer = session.query(Customer)\
                .filter(Customer.whatsapp_id == whatsapp_id)\
                .first()

            session.close()

            if customer:
                logging.debug(f"Retrieved customer info for {whatsapp_id}: {customer.name}")
                return customer.to_dict()
            else:
                logging.debug(f"No customer info found for {whatsapp_id}")
                return None

        except Exception as e:
            logging.error(f"Error getting customer info for {whatsapp_id}: {e}")
            return None

    def create_customer(self, whatsapp_id: str, name: str, age: Optional[int] = None) -> Optional[Dict]:
        """
        Create a new customer record.
        Note: One customer record per WhatsApp ID (business-agnostic).

        Args:
            whatsapp_id: WhatsApp ID (unique)
            name: Customer name
            age: Customer age (optional)

        Returns:
            Created customer information as dictionary, or None if failed
        """
        try:
            session: Session = get_db_session()

            # Create new customer record
            customer = Customer(
                whatsapp_id=whatsapp_id,
                name=name,
                age=age
            )

            session.add(customer)
            session.commit()

            customer_dict = customer.to_dict()
            session.close()

            logging.info(f"Created customer: {name} (WhatsApp: {whatsapp_id})")
            return customer_dict

        except IntegrityError as e:
            logging.warning(f"Customer already exists for WhatsApp ID {whatsapp_id}")
            # Customer already exists, try to update instead
            return self.update_customer(whatsapp_id, name, age)
        except Exception as e:
            logging.error(f"Error creating customer for {whatsapp_id}: {e}")
            return None

    def update_customer(self, whatsapp_id: str, name: str = None, age: int = None) -> Optional[Dict]:
        """
        Update existing customer information.

        Args:
            whatsapp_id: WhatsApp ID
            name: New customer name (optional)
            age: New customer age (optional)

        Returns:
            Updated customer information as dictionary, or None if failed
        """
        try:
            session: Session = get_db_session()

            customer = session.query(Customer)\
                .filter(Customer.whatsapp_id == whatsapp_id)\
                .first()

            if not customer:
                session.close()
                logging.warning(f"No customer found to update for WhatsApp ID {whatsapp_id}")
                return None

            # Update fields if provided
            if name is not None:
                customer.name = name
            if age is not None:
                customer.age = age

            session.commit()
            customer_dict = customer.to_dict()
            session.close()

            logging.info(f"Updated customer: {customer.name} (WhatsApp: {whatsapp_id})")
            return customer_dict

        except Exception as e:
            logging.error(f"Error updating customer for {whatsapp_id}: {e}")
            return None

    def create_or_update_customer(self, whatsapp_id: str, name: str, age: Optional[int] = None) -> Optional[Dict]:
        """
        Create a new customer or update existing one.

        Args:
            whatsapp_id: WhatsApp ID
            name: Customer name
            age: Customer age (optional)

        Returns:
            Customer information as dictionary, or None if failed
        """
        # Try to get existing customer first
        existing_customer = self.get_customer(whatsapp_id)

        if existing_customer:
            # Update existing customer
            return self.update_customer(whatsapp_id, name, age)
        else:
            # Create new customer
            return self.create_customer(whatsapp_id, name, age)

    def get_all_customers(self, limit: int = 100) -> List[Dict]:
        """
        Get all customers (for admin purposes).

        Args:
            limit: Maximum number of customers to return

        Returns:
            List of customer information dictionaries
        """
        try:
            session: Session = get_db_session()

            customers = session.query(Customer)\
                .order_by(Customer.created_at.desc())\
                .limit(limit)\
                .all()

            customer_list = [customer.to_dict() for customer in customers]
            session.close()

            logging.debug(f"Retrieved {len(customer_list)} customers")
            return customer_list

        except Exception as e:
            logging.error(f"Error getting all customers: {e}")
            return []

    def delete_customer(self, whatsapp_id: str) -> bool:
        """
        Delete a customer record.

        Args:
            whatsapp_id: WhatsApp ID

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            session: Session = get_db_session()

            deleted_count = session.query(Customer)\
                .filter(Customer.whatsapp_id == whatsapp_id)\
                .delete()

            session.commit()
            session.close()

            if deleted_count > 0:
                logging.info(f"Deleted customer for WhatsApp ID {whatsapp_id}")
                return True
            else:
                logging.warning(f"No customer found to delete for WhatsApp ID {whatsapp_id}")
                return False

        except Exception as e:
            logging.error(f"Error deleting customer for {whatsapp_id}: {e}")
            return False

    def get_customer_count(self) -> int:
        """
        Get total number of customers.

        Returns:
            Number of customers in database
        """
        try:
            session: Session = get_db_session()
            count = session.query(Customer).count()
            session.close()
            return count

        except Exception as e:
            logging.error(f"Error getting customer count: {e}")
            return 0

# Global instance
customer_service = CustomerService()