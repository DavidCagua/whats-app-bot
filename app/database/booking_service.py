"""
Service for bookings and business availability.
Handles creating, listing, updating bookings and managing availability slots.
"""

import logging
import random
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from .models import Booking, BusinessAvailability, Customer, StaffMember, get_db_session

logger = logging.getLogger(__name__)


class BookingService:
    """Service for managing bookings and business availability."""

    @staticmethod
    def _staff_free_for_bookings(
        bookings: List[Booking],
        interval_start: datetime,
        interval_end: datetime,
        staff_uuid: uuid.UUID,
    ) -> bool:
        """
        True if staff_uuid has no blocking overlap on [interval_start, interval_end).
        Bookings with staff_member_id NULL (legacy) block every staff for that interval.
        """
        for b in bookings:
            if interval_end <= b.start_at or interval_start >= b.end_at:
                continue
            if b.staff_member_id is None or b.staff_member_id == staff_uuid:
                return False
        return True

    def _validate_staff_member(
        self, session: Session, business_id: str, staff_member_id: str
    ) -> bool:
        row = (
            session.query(StaffMember)
            .filter(
                StaffMember.id == uuid.UUID(staff_member_id),
                StaffMember.business_id == uuid.UUID(business_id),
                StaffMember.is_active == True,
            )
            .first()
        )
        return row is not None

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
        Optional: customer_id, service_name, status, notes, created_via, staff_member_id
        """
        try:
            session: Session = get_db_session()

            staff_mid = data.get("staff_member_id")
            if staff_mid:
                if not self._validate_staff_member(session, data["business_id"], str(staff_mid)):
                    session.close()
                    logger.warning(
                        "Invalid or inactive staff_member_id for business %s",
                        data["business_id"],
                    )
                    return None

            booking = Booking(
                business_id=uuid.UUID(data["business_id"]),
                customer_id=data.get("customer_id"),
                service_name=data.get("service_name"),
                start_at=datetime.fromisoformat(data["start_at"]),
                end_at=datetime.fromisoformat(data["end_at"]),
                status=data.get("status", "confirmed"),
                notes=data.get("notes"),
                created_via=data.get("created_via", "admin"),
                staff_member_id=uuid.UUID(str(staff_mid)) if staff_mid else None,
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
        ALLOWED_FIELDS = {
            "status",
            "notes",
            "service_name",
            "start_at",
            "end_at",
            "customer_id",
            "staff_member_id",
        }
        try:
            session: Session = get_db_session()

            booking = session.query(Booking).filter(
                Booking.id == uuid.UUID(booking_id)
            ).first()

            if not booking:
                session.close()
                return None

            if "staff_member_id" in data and data["staff_member_id"] is not None:
                sid = str(data["staff_member_id"])
                if not self._validate_staff_member(session, str(booking.business_id), sid):
                    session.close()
                    logger.warning("Invalid staff_member_id on update_booking")
                    return None

            for field in ALLOWED_FIELDS:
                if field in data:
                    value = data[field]
                    if field in ("start_at", "end_at") and isinstance(value, str):
                        value = datetime.fromisoformat(value)
                    if field == "staff_member_id":
                        if value is None:
                            setattr(booking, field, None)
                        else:
                            setattr(booking, field, uuid.UUID(str(value)))
                        continue
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

    def get_available_slots(
        self,
        business_id: str,
        date_str: str,
        staff_member_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        Return time slots for a business on a given date.

        Args:
            business_id: Business UUID
            date_str: "YYYY-MM-DD"
            staff_member_id: If set, "available" is for that staff only.
                If None, aggregate mode: available if at least one active staff is free;
                each slot may include "free_staff_ids".

        Returns:
            List of dicts with start, end, start_at, end_at, available,
            and optionally free_staff_ids (list of UUID strings).
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

            # Build all slots (open_time/close_time may be datetime.time or "HH:MM" string)
            import datetime as _dt
            def _parse_time(t):
                if isinstance(t, _dt.time):
                    return t.hour, t.minute
                return map(int, str(t).split(":"))
            open_h, open_m = _parse_time(avail.open_time)
            close_h, close_m = _parse_time(avail.close_time)
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

            from app.services.staff_service import staff_service

            staff_rows = staff_service.get_staff_by_business(business_id, active_only=True)
            staff_ids = [s["id"] for s in staff_rows]

            slots = []
            while slot_start + timedelta(minutes=slot_mins) <= close_dt:
                slot_end = slot_start + timedelta(minutes=slot_mins)
                if staff_member_id:
                    su = uuid.UUID(staff_member_id)
                    avail = self._staff_free_for_bookings(
                        existing, slot_start, slot_end, su
                    )
                    slots.append({
                        "start": slot_start.strftime("%H:%M"),
                        "end": slot_end.strftime("%H:%M"),
                        "start_at": slot_start.isoformat(),
                        "end_at": slot_end.isoformat(),
                        "available": avail,
                    })
                else:
                    free_staff_ids = [
                        sid
                        for sid in staff_ids
                        if self._staff_free_for_bookings(
                            existing, slot_start, slot_end, uuid.UUID(sid)
                        )
                    ]
                    slots.append({
                        "start": slot_start.strftime("%H:%M"),
                        "end": slot_end.strftime("%H:%M"),
                        "start_at": slot_start.isoformat(),
                        "end_at": slot_end.isoformat(),
                        "available": len(free_staff_ids) > 0,
                        "free_staff_ids": free_staff_ids,
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

    def is_interval_free_for_staff(
        self,
        business_id: str,
        start_dt: datetime,
        end_dt: datetime,
        staff_member_id: str,
    ) -> bool:
        """True if the staff member has no blocking overlap (incl. legacy NULL-staff bookings)."""
        try:
            session: Session = get_db_session()
            if not self._validate_staff_member(session, business_id, staff_member_id):
                session.close()
                return False
            bookings = session.query(Booking).filter(
                and_(
                    Booking.business_id == uuid.UUID(business_id),
                    Booking.status.notin_(["cancelled"]),
                    Booking.start_at < end_dt,
                    Booking.end_at > start_dt,
                )
            ).all()
            session.close()
            return self._staff_free_for_bookings(
                bookings, start_dt, end_dt, uuid.UUID(staff_member_id)
            )
        except Exception as e:
            logger.error(f"is_interval_free_for_staff error: {e}")
            return False

    def pick_random_free_staff_for_interval(
        self,
        business_id: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> Optional[str]:
        """Uniform random choice among active staff free for [start_dt, end_dt). None if none."""
        try:
            from app.services.staff_service import staff_service

            staff_rows = staff_service.get_staff_by_business(business_id, active_only=True)
            if not staff_rows:
                return None
            session: Session = get_db_session()
            bookings = session.query(Booking).filter(
                and_(
                    Booking.business_id == uuid.UUID(business_id),
                    Booking.status.notin_(["cancelled"]),
                    Booking.start_at < end_dt,
                    Booking.end_at > start_dt,
                )
            ).all()
            session.close()
            candidates = [
                s["id"]
                for s in staff_rows
                if self._staff_free_for_bookings(
                    bookings, start_dt, end_dt, uuid.UUID(s["id"])
                )
            ]
            if not candidates:
                return None
            return random.choice(candidates)
        except Exception as e:
            logger.error(f"pick_random_free_staff_for_interval error: {e}")
            return None

    def list_customer_bookings(
        self,
        whatsapp_id: str,
        business_id: Optional[str] = None,
        upcoming_only: bool = True,
    ) -> List[Dict]:
        """
        List bookings for a customer identified by whatsapp_id.
        Optionally scoped to a business and/or only future bookings.
        """
        try:
            from .customer_service import customer_service as cs
            customer = cs.get_customer_by_whatsapp_id(whatsapp_id)
            if not customer:
                return []

            session: Session = get_db_session()
            q = session.query(Booking).filter(
                Booking.customer_id == customer["id"],
                Booking.status.notin_(["cancelled"]),
            )

            if business_id:
                q = q.filter(Booking.business_id == uuid.UUID(business_id))

            if upcoming_only:
                q = q.filter(Booking.start_at >= datetime.now(tz=timezone.utc))

            bookings = q.order_by(Booking.start_at.asc()).all()
            result = [b.to_dict() for b in bookings]
            session.close()
            return result

        except Exception as e:
            logger.error(f"Error listing bookings for whatsapp_id {whatsapp_id}: {e}")
            return []


booking_service = BookingService()
