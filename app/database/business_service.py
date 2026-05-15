"""
Database service for managing business operations in multi-tenant system.
Handles businesses, WhatsApp numbers, users, and their relationships.
"""

import logging
import re
import threading
import time
from typing import Optional, Dict, List, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_
import uuid
from .models import Business, WhatsappNumber, User, UserBusiness, get_db_session


# ── In-process TTL cache for phone → business_context ───────────────────
# The tenant → Twilio-number binding is effectively immutable on the
# webhook hot path. Hitting Supabase on every inbound message was adding
# 3–5 s to the first reply; a simple module-level dict with a 5-minute
# TTL eliminates the round trip for all warm requests.
#
# Scope: used by `get_business_context_by_phone_number` (inbound webhook
# routing) and `get_business_context_by_business_id` (voice-reply worker,
# admin UI hot reads). Writes through create_whatsapp_number /
# update_whatsapp_number invalidate both caches.
_PHONE_CTX_CACHE_TTL = 300.0  # seconds
_phone_ctx_cache: Dict[str, Tuple[float, Optional[Dict]]] = {}
_business_id_ctx_cache: Dict[str, Tuple[float, Optional[Dict]]] = {}
_phone_ctx_lock = threading.Lock()


def _canonical_phone(phone: str) -> str:
    """
    Canonicalize a phone string to ``+<digits>`` form.

    Used for both the DB write path (so stored values are consistent)
    and the lookup path (so the incoming `To` number hits the unique
    index on whatsapp_numbers.phone_number added in migration 024).
    """
    if not phone:
        return ""
    s = str(phone).strip().lower()
    if s.startswith("whatsapp:"):
        s = s[9:].strip()
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return ""
    return "+" + digits


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

    def get_business_settings_fresh(self, business_id: str) -> Dict:
        """
        Fetch ``businesses.settings`` directly from the DB, bypassing the
        ``_phone_ctx_cache`` / ``_business_id_ctx_cache`` snapshot.

        Used at the top of every WhatsApp turn for operator-controlled
        toggles that need to take effect immediately — e.g. the
        delivery-paused switch and ETA override on the orders page.
        Caching the full business_context for 5 min is fine for static
        fields (name, whatsapp_number_id) but unacceptable for
        operational kill-switches: the operator would have to wait up
        to 5 min or restart the runtime for a toggle to land.

        Single indexed PK lookup, negligible cost. Returns ``{}`` on
        miss / error so callers can merge without None-checks.
        """
        try:
            session: Session = get_db_session()
            try:
                business = (
                    session.query(Business)
                    .filter(Business.id == uuid.UUID(business_id))
                    .first()
                )
                if business and business.settings:
                    return dict(business.settings)
                return {}
            finally:
                session.close()
        except Exception as exc:
            logging.warning(
                f"[BUSINESS_SETTINGS_FRESH] business={business_id} fetch failed: {exc}"
            )
            return {}

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
            settings: Business settings (JSONB). Prefer {} — do not use legacy
                business_hours or appointment_settings; use business_availability instead.

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

    # Kept for backwards compatibility with callers that expected the
    # old helper name. New code should use the module-level
    # _canonical_phone directly.
    def _normalize_phone_for_lookup(self, phone: str) -> str:
        return _canonical_phone(phone)

    def get_whatsapp_number_by_phone_number(self, phone: str) -> Optional[Dict]:
        """
        Get WhatsApp number by phone number (E.164).
        Used for routing both Meta and Twilio webhooks — single lookup key.

        Indexed direct lookup: canonicalize the input, then hit the
        partial unique index added in migration 024
        (whatsapp_numbers_phone_number_active_unique). One round trip,
        O(log n) regardless of tenant count.

        Args:
            phone: E.164 number (e.g. whatsapp:+573126783216 or +573126783216)

        Returns:
            WhatsApp number info with business_id, or None if not found.
        """
        try:
            normalized = _canonical_phone(phone)
            if not normalized:
                return None

            session: Session = get_db_session()
            try:
                wn = session.query(WhatsappNumber).filter(
                    WhatsappNumber.phone_number == normalized,
                    WhatsappNumber.is_active == True,
                ).first()
                if not wn:
                    logging.warning(f"No active WhatsApp number found for {phone}")
                    return None
                return wn.to_dict()
            finally:
                session.close()

        except Exception as e:
            logging.error(f"Error getting WhatsApp number by phone: {e}")
            return None

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

        Args:
            business_id: Business UUID
            phone_number_id: Meta's phone number ID, or "twilio:+15556738752" for Twilio
            phone_number: E.164 number (e.g., +15556738752)
            display_name: Optional friendly name (e.g., "Main Line", "Support Line")

        Returns:
            Created WhatsApp number information, or None if failed
        """
        try:
            canonical = _canonical_phone(phone_number)
            session: Session = get_db_session()

            whatsapp_number = WhatsappNumber(
                business_id=uuid.UUID(business_id),
                phone_number_id=phone_number_id,
                phone_number=canonical or phone_number,
                display_name=display_name,
                is_active=True
            )

            session.add(whatsapp_number)
            session.commit()

            whatsapp_dict = whatsapp_number.to_dict()
            session.close()

            # Invalidate any cached negative lookup for this number so
            # the first webhook after provisioning hits the DB fresh.
            self.invalidate_phone_cache(canonical)
            self.invalidate_business_cache(business_id)

            logging.info(f"Created WhatsApp number {canonical} (ID: {phone_number_id}) for business {business_id}")
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

            # Capture the old canonical number so we can invalidate its
            # cache entry even if the phone_number itself changes.
            old_canonical = _canonical_phone(whatsapp_number.phone_number)

            if phone_number is not None:
                whatsapp_number.phone_number = _canonical_phone(phone_number) or phone_number
            if display_name is not None:
                whatsapp_number.display_name = display_name
            if is_active is not None:
                whatsapp_number.is_active = is_active

            session.commit()
            whatsapp_dict = whatsapp_number.to_dict()
            new_canonical = _canonical_phone(whatsapp_dict.get("phone_number"))
            session.close()

            # Drop both old and new cache keys so deactivation / rename /
            # reassignment takes effect immediately.
            self.invalidate_phone_cache(old_canonical)
            if new_canonical and new_canonical != old_canonical:
                self.invalidate_phone_cache(new_canonical)
            # Business_id binding may have changed (e.g. number moved
            # between tenants) — nuke the business_id cache as well.
            if whatsapp_dict.get("business_id"):
                self.invalidate_business_cache(whatsapp_dict["business_id"])

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
                           role: str = "member") -> Optional[Dict]:
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

    def get_business_context_by_phone_number(self, phone: str) -> Optional[Dict]:
        """
        Get business context by phone number (unified for Meta and Twilio).
        Infers send path from phone_number_id: if it starts with "twilio:" -> Twilio API.

        Hot path optimization:
          1. Canonicalize input and check the module-level TTL cache.
          2. On miss, run ONE query that joins whatsapp_numbers to
             businesses (was two sequential sessions), hitting the
             unique index from migration 024.
          3. Cache the result (including negative results) for 5 min.

        Args:
            phone: E.164 number (from Meta metadata.display_phone_number or Twilio To)

        Returns:
            Context dict with business, phone_number_id or provider/twilio_phone_number.
        """
        normalized = _canonical_phone(phone)
        if not normalized:
            return None

        now = time.time()
        with _phone_ctx_lock:
            cached = _phone_ctx_cache.get(normalized)
            if cached and (now - cached[0]) < _PHONE_CTX_CACHE_TTL:
                return cached[1]

        context: Optional[Dict] = None
        try:
            session: Session = get_db_session()
            try:
                # Single round trip: indexed whatsapp_numbers lookup +
                # eager-loaded Business via SQL JOIN. Was two sequential
                # sessions → now one.
                wn = (
                    session.query(WhatsappNumber)
                    .options(joinedload(WhatsappNumber.business))
                    .filter(
                        WhatsappNumber.phone_number == normalized,
                        WhatsappNumber.is_active == True,
                    )
                    .first()
                )
                if not wn:
                    logging.warning(f"No active WhatsApp number found for {phone}")
                elif not wn.business:
                    logging.error(f"No business found for WhatsApp number {wn.id}")
                else:
                    whatsapp_number = wn.to_dict()
                    business = wn.business.to_dict()

                    pnid_str = str(whatsapp_number.get("phone_number_id") or "").strip()
                    phone_val = whatsapp_number.get("phone_number", "")
                    is_twilio = pnid_str.startswith("twilio:") or (not pnid_str and phone_val)

                    context = {
                        "business": business,
                        "whatsapp_number": whatsapp_number,
                        "business_id": business["id"],
                        "whatsapp_number_id": whatsapp_number["id"],
                    }
                    if is_twilio:
                        context["provider"] = "twilio"
                        context["twilio_phone_number"] = (
                            f"whatsapp:{phone_val}"
                            if phone_val and not str(phone_val).startswith("whatsapp:")
                            else (phone_val or "")
                        )
                    else:
                        context["phone_number_id"] = pnid_str or whatsapp_number.get("phone_number_id")

                    logging.info(f"[CONTEXT] Loaded context for business: {business['name']}")
            finally:
                session.close()
        except Exception as e:
            logging.error(f"Error getting business context by phone number: {e}")
            return None

        # Cache positive AND negative results. Negatives are cheap and
        # protect against a flood of lookups for an unconfigured number.
        with _phone_ctx_lock:
            _phone_ctx_cache[normalized] = (now, context)

        return context

    @staticmethod
    def invalidate_phone_cache(phone: Optional[str] = None) -> None:
        """
        Drop cached phone→context entries. Called from the write path
        (create/update/delete whatsapp_numbers) so admins toggling a
        number see their change immediately instead of waiting for the
        5 min TTL. Pass no argument to clear both phone- and
        business_id-keyed caches entirely.
        """
        with _phone_ctx_lock:
            if phone is None:
                _phone_ctx_cache.clear()
                _business_id_ctx_cache.clear()
                return
            normalized = _canonical_phone(phone)
            _phone_ctx_cache.pop(normalized, None)

    @staticmethod
    def invalidate_business_cache(business_id: Optional[str] = None) -> None:
        """
        Drop the cached business_id→context entry. Called from any
        write path that changes business settings / whatsapp-number
        routing so admin updates are visible immediately. Pass no
        argument to clear the entire business_id cache.
        """
        with _phone_ctx_lock:
            if business_id is None:
                _business_id_ctx_cache.clear()
                return
            _business_id_ctx_cache.pop(str(business_id), None)

    def get_business_context_by_business_id(self, business_id: str) -> Optional[Dict]:
        """
        Get business context using only business_id.
        Picks an active WhatsApp number; uses phone_number_id if set,
        else phone_number (E.164) for lookup.

        Cached for 5 minutes in _business_id_ctx_cache because the
        voice-reply worker and ``run_agent_and_send_reply`` both call
        this on every transcript-ready callback — previously one DB
        round trip per voice message.
        """
        if not business_id:
            return None

        key = str(business_id)
        now = time.time()
        with _phone_ctx_lock:
            cached = _business_id_ctx_cache.get(key)
            if cached and (now - cached[0]) < _PHONE_CTX_CACHE_TTL:
                return cached[1]

        context: Optional[Dict] = None
        try:
            numbers = self.get_business_whatsapp_numbers(business_id)
            if numbers:
                # Prefer active numbers if present; fallback to first entry.
                active = [n for n in numbers if n.get("is_active")]
                chosen = active[0] if active else numbers[0]
                phone_number_id = chosen.get("phone_number_id")
                if phone_number_id:
                    context = self.get_business_context(phone_number_id)
                else:
                    # phone_number_id null (e.g. only phone_number stored) — resolve by phone number
                    phone_number = chosen.get("phone_number")
                    if phone_number:
                        context = self.get_business_context_by_phone_number(phone_number)
        except Exception as e:
            logging.error(f"Error getting business context by business_id: {e}")
            return None

        with _phone_ctx_lock:
            _business_id_ctx_cache[key] = (now, context)
        return context


# Global instance
business_service = BusinessService()
