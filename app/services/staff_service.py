"""
Service for managing staff members in a business.
Handles staff member CRUD operations and staff prompts for AI agents.
"""

import logging
from typing import Optional, Dict, List
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import uuid
from app.database.models import StaffMember, get_db_session


class StaffService:
    """Service for managing staff members."""

    def __init__(self):
        """Initialize the staff service."""
        logging.info("StaffService initialized")

    # ========================================================================
    # STAFF MEMBER CRUD OPERATIONS
    # ========================================================================

    def get_staff_member(self, staff_id: str) -> Optional[Dict]:
        """
        Get a staff member by ID.

        Args:
            staff_id: Staff member UUID

        Returns:
            Staff member information as dictionary, or None if not found
        """
        try:
            session: Session = get_db_session()

            staff = session.query(StaffMember)\
                .filter(StaffMember.id == uuid.UUID(staff_id))\
                .first()

            session.close()

            if staff:
                logging.debug(f"Retrieved staff member: {staff.name}")
                return staff.to_dict()
            else:
                logging.debug(f"No staff member found with ID {staff_id}")
                return None

        except Exception as e:
            logging.error(f"Error getting staff member {staff_id}: {e}")
            return None

    def get_staff_by_business(self, business_id: str, active_only: bool = False) -> List[Dict]:
        """
        Get all staff members for a business.

        Args:
            business_id: Business UUID
            active_only: If True, return only active staff members

        Returns:
            List of staff member information as dictionaries
        """
        try:
            session: Session = get_db_session()

            query = session.query(StaffMember)\
                .filter(StaffMember.business_id == uuid.UUID(business_id))

            if active_only:
                query = query.filter(StaffMember.is_active == True)

            staff_list = query.order_by(StaffMember.name).all()
            result = [s.to_dict() for s in staff_list]

            session.close()

            logging.debug(f"Retrieved {len(result)} staff members for business {business_id}")
            return result

        except Exception as e:
            logging.error(f"Error getting staff by business: {e}")
            return []

    def create_staff_member(self, business_id: str, name: str, role: str,
                           user_id: Optional[str] = None) -> Optional[Dict]:
        """
        Create a new staff member.

        Args:
            business_id: Business UUID
            name: Staff member name
            role: Staff member role/position (e.g., 'barber', 'hairdresser')
            user_id: Optional linked user UUID

        Returns:
            Created staff member information as dictionary, or None if failed
        """
        try:
            session: Session = get_db_session()

            staff = StaffMember(
                business_id=uuid.UUID(business_id),
                name=name,
                role=role,
                user_id=uuid.UUID(user_id) if user_id else None,
                is_active=True
            )

            session.add(staff)
            session.commit()

            staff_dict = staff.to_dict()
            session.close()

            logging.info(f"Created staff member: {name} (ID: {staff_dict['id']}) for business {business_id}")
            return staff_dict

        except IntegrityError as e:
            logging.error(f"Staff member integrity error: {e}")
            return None
        except Exception as e:
            logging.error(f"Error creating staff member: {e}")
            return None

    def update_staff_member(self, staff_id: str, **kwargs) -> Optional[Dict]:
        """
        Update a staff member.

        Args:
            staff_id: Staff member UUID
            **kwargs: Fields to update (name, role, is_active, user_id)

        Returns:
            Updated staff member information as dictionary, or None if failed
        """
        try:
            session: Session = get_db_session()

            staff = session.query(StaffMember)\
                .filter(StaffMember.id == uuid.UUID(staff_id))\
                .first()

            if not staff:
                session.close()
                logging.warning(f"No staff member found to update with ID {staff_id}")
                return None

            # Update allowed fields
            allowed_fields = {'name', 'role', 'is_active', 'user_id'}
            for key, value in kwargs.items():
                if key in allowed_fields:
                    if key == 'user_id' and value:
                        setattr(staff, key, uuid.UUID(value))
                    elif key == 'user_id' and not value:
                        setattr(staff, key, None)
                    else:
                        setattr(staff, key, value)

            session.commit()
            staff_dict = staff.to_dict()
            session.close()

            logging.info(f"Updated staff member: {staff_dict['name']}")
            return staff_dict

        except Exception as e:
            logging.error(f"Error updating staff member: {e}")
            return None

    def delete_staff_member(self, staff_id: str) -> bool:
        """
        Delete a staff member.

        Args:
            staff_id: Staff member UUID

        Returns:
            True if deleted, False otherwise
        """
        try:
            session: Session = get_db_session()

            staff = session.query(StaffMember)\
                .filter(StaffMember.id == uuid.UUID(staff_id))\
                .first()

            if not staff:
                session.close()
                logging.warning(f"No staff member found to delete with ID {staff_id}")
                return False

            session.delete(staff)
            session.commit()
            session.close()

            logging.info(f"Deleted staff member with ID {staff_id}")
            return True

        except Exception as e:
            logging.error(f"Error deleting staff member: {e}")
            return False

    # ========================================================================
    # PROMPT GENERATION FOR AI AGENTS
    # ========================================================================

    def get_staff_text_for_prompt(self, business_id: str) -> str:
        """
        Get formatted staff information for AI agent prompts.
        Returns a text representation suitable for inclusion in system prompts.

        Args:
            business_id: Business UUID

        Returns:
            Formatted staff text or empty string if no staff
        """
        try:
            staff_list = self.get_staff_by_business(business_id, active_only=True)

            if not staff_list:
                return ""

            lines = [
                "👥 **EQUIPO (usa el ID exacto en las herramientas de reserva)**",
                "",
            ]
            for staff in staff_list:
                lines.append(f"• ID `{staff['id']}` — {staff['name']} ({staff['role']})")

            return "\n".join(lines)

        except Exception as e:
            logging.error(f"Error getting staff text for prompt: {e}")
            return ""

    def get_staff_list_for_prompt(self, business_id: str) -> List[Dict]:
        """
        Get staff members formatted for AI agent context.

        Args:
            business_id: Business UUID

        Returns:
            List of staff member dicts with id, name, and role
        """
        try:
            staff_list = self.get_staff_by_business(business_id, active_only=True)
            return [
                {
                    'id': s['id'],
                    'name': s['name'],
                    'role': s['role']
                }
                for s in staff_list
            ]

        except Exception as e:
            logging.error(f"Error getting staff list for prompt: {e}")
            return []


# Global instance
staff_service = StaffService()
