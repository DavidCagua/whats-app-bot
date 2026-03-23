"""
Service for bookings and business availability.
Handles creating, listing, updating bookings and managing availability slots.
"""

import logging
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from .models import Booking, BusinessAvailability, Customer, get_db_session

logger = logging.getLogger(__name__)


class BookingService:
    """Service for managing bookings and business availability."""

    # ========================================================================
    # BOOKINGS
    # ========================================================================

    def list_bookings(
        self,
        business_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """
        List bookings for a business, optionally filtered by date range and status.

        Args:
            business_id: Business UUID
            date_from: ISO date string "YYYY-MM-DD" (inclusive)
            date_to:   ISO date string "YYYY-MM-DD" (inclusive)
            status:    Filter by booking status
            limit:     Max results

        Returns:
            List of booking dicts (with customer info embedded)
        """
        try:
            session: Session = get_db_session()

            q = session.query(Booking).filter(
                Booking.business_id == uuid.UUID(business_id)
            )

            if date_from:
                dt_from = datetime.fromisoformat(date_from).replace(
                    hour=0, minute=0, second=0, microsecond=0,
                    tzinfo=timezone.utc
                )
                q = q.filter(Booking.start_at >= dt_from)

            if date_to:
                dt_to = datetime.fromisoformat(date_to).replace(
                    hour=23, minute=59, second=59, microsecond=999999,
                    tzinfo=timezone.utc
                )
                q = q.filter(Booking.start_at <= dt_to)

            if status:
                q = q.filter(Booking.status == status)

            bookings = q.order_by(Booking.start_at.asc()).limit(limit).all()

            # Load customer info in one pass
            customer_ids = {b.customer_id for b in bookings if b.customer_id}
            customers = {}
            if customer_ids:
                rows = session.query(Customer).filter(Customer.id.in_(customer_ids)).all()
                customers = {c.id: c for c in rows}

            results = []
            for b in bookings:
                d = b.to_dict()
                cust = customers.get(b.customer_id)
                d["customer"] = cust.to_dict() if cust else None
                results.append(d)

            session.close()
            return results

        except Exception as e:
            logger.error(f"Error listing bookings for business {business_id}: {e}")
            return []

    def get_booking(self, booking_id: str) -> Optional[Dict]:
        """Get a single booking by ID, with customer info."""
        try:
            session: Session = get_db_session()
            booking = session.query(Booking).filter(
                Booking.id == uuid.UUID(booking_id)
            ).first()

            if not booking:
                session.close()
                return None

            d = booking.to_dict()
            if booking.customer_id:
                cust = session.query(Customer).filter(Customer.id == booking.customer_id).first()
                d["customer"] = cust.to_dict() if cust else None
            else:
                d["customer"] = None

            session.close()
            return d

        except Exception as e:
            logger.error(f"Error getting booking {booking_id}: {e}")
            return None

    def create_booking(self, data: Dict) -> Optional[Dict]:
        """
        Create a new booking.

        Required fields: business_id, start_at, end_at
        Optional: customer_id, service_name, status, notes, created_via
        """
        try:
            session: Session = get_db_session()

            booking = Booking(
                business_id=uuid.UUID(data["business_id"]),
                customer_id=data.get("customer_id"),
                service_name=data.get("service_name"),
                start_at=datetime.fromisoformat(data["start_at"]),
                end_at=datetime.fromisoformat(data["end_at"]),
                status=data.get("status", "confirmed"),
                notes=data.get("notes"),
                created_via=data.get("created_via", "admin"),
            )

            session.add(booking)
            session.commit()
            session.refresh(booking)

            result = booking.to_dict()
            if booking.customer_id:
                cust = session.query(Customer).filter(Customer.id == booking.customer_id).first()
                result["customer"] = cust.to_dict() if cust else None
            else:
                result["customer"] = None

            session.close()
            logger.info(f"Created booking {booking.id} for business {data['business_id']}")
            return result

        except Exception as e:
            logger.error(f"Error creating booking: {e}")
            if "session" in locals():
                session.rollback()
                session.close()
            return None

    def update_booking(self, booking_id: str, data: Dict) -> Optional[Dict]:
        """
        Partially update a booking (status, notes, service_name, start_at, end_at).
        Returns updated booking dict or None if not found.
        """
        ALLOWED_FIELDS = {"status", "notes", "service_name", "start_at", "end_at", "customer_id"}
        try:
            session: Session = get_db_session()

            booking = session.query(Booking).filter(
                Booking.id == uuid.UUID(booking_id)
            ).first()

            if not booking:
                session.close()
                return None

            for field in ALLOWED_FIELDS:
                if field in data:
                    value = data[field]
                    if field in ("start_at", "end_at") and isinstance(value, str):
                        value = datetime.fromisoformat(value)
                    setattr(booking, field, value)

            booking.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(booking)

            result = booking.to_dict()
            if booking.customer_id:
                cust = session.query(Customer).filter(Customer.id == booking.customer_id).first()
                result["customer"] = cust.to_dict() if cust else None
            else:
                result["customer"] = None

            session.close()
            logger.info(f"Updated booking {booking_id}: {data}")
            return result

        except Exception as e:
            logger.error(f"Error updating booking {booking_id}: {e}")
            if "session" in locals():
                session.rollback()
                session.close()
            return None

    # ========================================================================
    # AVAILABILITY
    # ========================================================================

    def get_availability(self, business_id: str) -> List[Dict]:
        """Get all availability rules for a business."""
        try:
            session: Session = get_db_session()
            rows = session.query(BusinessAvailability).filter(
                BusinessAvailability.business_id == uuid.UUID(business_id)
            ).order_by(BusinessAvailability.day_of_week).all()
            result = [r.to_dict() for r in rows]
            session.close()
            return result
        except Exception as e:
            logger.error(f"Error getting availability for {business_id}: {e}")
            return []

    def get_available_slots(self, business_id: str, date_str: str) -> List[Dict]:
        """
        Return available time slots for a business on a given date.

        Args:
            business_id: Business UUID
            date_str: "YYYY-MM-DD"

        Returns:
            List of {"start": "HH:MM", "end": "HH:MM", "available": bool}
        """
        try:
            target_date = date.fromisoformat(date_str)
            day_of_week = target_date.weekday()  # 0=Monday … 6=Sunday
            # Convert to Sunday=0 convention used in DB
            day_of_week_db = (day_of_week + 1) % 7  # Mon=1 … Sun=0

            session: Session = get_db_session()

            avail = session.query(BusinessAvailability).filter(
                and_(
                    BusinessAvailability.business_id == uuid.UUID(business_id),
                    BusinessAvailability.day_of_week == day_of_week_db,
                    BusinessAvailability.is_active == True,
                )
            ).first()

            if not avail:
                session.close()
                return []  # Business is closed this day

            # Build all slots
            open_h, open_m = map(int, avail.open_time.split(":"))
            close_h, close_m = map(int, avail.close_time.split(":"))
            slot_mins = avail.slot_duration_minutes

            slot_start = datetime(
                target_date.year, target_date.month, target_date.day,
                open_h, open_m, tzinfo=timezone.utc
            )
            close_dt = datetime(
                target_date.year, target_date.month, target_date.day,
                close_h, close_m, tzinfo=timezone.utc
            )

            # Fetch existing bookings for this day
            day_start = datetime(target_date.year, target_date.month, target_date.day,
                                 0, 0, 0, tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)

            existing = session.query(Booking).filter(
                and_(
                    Booking.business_id == uuid.UUID(business_id),
                    Booking.start_at >= day_start,
                    Booking.start_at < day_end,
                    Booking.status.notin_(["cancelled"]),
                )
            ).all()

            booked_ranges = [(b.start_at, b.end_at) for b in existing]

            slots = []
            while slot_start + timedelta(minutes=slot_mins) <= close_dt:
                slot_end = slot_start + timedelta(minutes=slot_mins)
                occupied = any(
                    not (slot_end <= bs or slot_start >= be)
                    for bs, be in booked_ranges
                )
                slots.append({
                    "start": slot_start.strftime("%H:%M"),
                    "end": slot_end.strftime("%H:%M"),
                    "start_at": slot_start.isoformat(),
                    "end_at": slot_end.isoformat(),
                    "available": not occupied,
                })
                slot_start = slot_end

            session.close()
            return slots

        except Exception as e:
            logger.error(f"Error getting slots for {business_id} on {date_str}: {e}")
            return []

    def upsert_availability(self, business_id: str, rules: List[Dict]) -> List[Dict]:
        """
        Upsert availability rules for a business.
        Each rule: {day_of_week, open_time, close_time, slot_duration_minutes, is_active}
        """
        try:
            session: Session = get_db_session()
            results = []

            for rule in rules:
                existing = session.query(BusinessAvailability).filter(
                    and_(
                        BusinessAvailability.business_id == uuid.UUID(business_id),
                        BusinessAvailability.day_of_week == rule["day_of_week"],
                    )
                ).first()

                if existing:
                    existing.open_time = rule.get("open_time", existing.open_time)
                    existing.close_time = rule.get("close_time", existing.close_time)
                    existing.slot_duration_minutes = rule.get(
                        "slot_duration_minutes", existing.slot_duration_minutes
                    )
                    existing.is_active = rule.get("is_active", existing.is_active)
                    existing.updated_at = datetime.utcnow()
                    results.append(existing)
                else:
                    new_rule = BusinessAvailability(
                        business_id=uuid.UUID(business_id),
                        day_of_week=rule["day_of_week"],
                        open_time=rule["open_time"],
                        close_time=rule["close_time"],
                        slot_duration_minutes=rule.get("slot_duration_minutes", 60),
                        is_active=rule.get("is_active", True),
                    )
                    session.add(new_rule)
                    results.append(new_rule)

            session.commit()
            for r in results:
                session.refresh(r)
            out = [r.to_dict() for r in results]
            session.close()
            return out

        except Exception as e:
            logger.error(f"Error upserting availability for {business_id}: {e}")
            if "session" in locals():
                session.rollback()
                session.close()
            return []


booking_service = BookingService()
