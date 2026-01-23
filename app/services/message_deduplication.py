"""
Message Deduplication Service

Prevents processing the same WhatsApp message twice by tracking processed message IDs.
Uses database for persistent storage if available, falls back to in-memory LRU cache with TTL for local dev.
"""

import logging
import os
from typing import Optional
from datetime import datetime, timedelta
from collections import OrderedDict

try:
    from app.database.models import get_db_session, Base, engine
    from sqlalchemy import Column, String, DateTime, Index, text
    from sqlalchemy.dialects.postgresql import UUID
    import uuid
    DB_AVAILABLE = True
except (ImportError, AttributeError) as e:
    DB_AVAILABLE = False
    engine = None
    Base = None
    logging.warning(f"[DEDUPE] Database not available, using in-memory cache only: {e}")


# Define ProcessedMessage model only if database is available
if DB_AVAILABLE and Base is not None:
    class ProcessedMessage(Base):
        """Database model for tracking processed message IDs."""
        __tablename__ = 'processed_messages'

        id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        message_id = Column(String(255), nullable=False, unique=True, index=True)
        processed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

        # Index for cleanup queries
        __table_args__ = (
            Index('idx_processed_at', 'processed_at'),
        )
else:
    ProcessedMessage = None


class LRUCacheWithTTL:
    """
    In-memory LRU cache with TTL for message ID deduplication.
    Used as fallback when database is not available.
    """
    def __init__(self, max_size: int = 10000, ttl_seconds: int = 86400):
        """
        Args:
            max_size: Maximum number of entries (evicts oldest when full)
            ttl_seconds: Time-to-live in seconds (default 24 hours)
        """
        self.cache = OrderedDict()
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds

    def _is_expired(self, timestamp: datetime) -> bool:
        """Check if entry has expired based on TTL."""
        return (datetime.utcnow() - timestamp).total_seconds() > self.ttl_seconds

    def has(self, message_id: str) -> bool:
        """Check if message ID exists and is not expired."""
        if message_id not in self.cache:
            return False

        timestamp, _ = self.cache[message_id]
        if self._is_expired(timestamp):
            # Remove expired entry
            del self.cache[message_id]
            return False

        # Move to end (most recently used)
        self.cache.move_to_end(message_id)
        return True

    def add(self, message_id: str):
        """Add message ID with current timestamp."""
        # Remove if exists (to update position)
        if message_id in self.cache:
            self.cache.move_to_end(message_id)
        else:
            # Evict oldest if at capacity
            if len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)  # Remove oldest

        self.cache[message_id] = (datetime.utcnow(), True)

    def cleanup_expired(self):
        """Remove expired entries (called periodically)."""
        expired_keys = [
            key for key, (timestamp, _) in self.cache.items()
            if self._is_expired(timestamp)
        ]
        for key in expired_keys:
            del self.cache[key]
        return len(expired_keys)


class MessageDeduplicationService:
    """
    Service for tracking and checking processed WhatsApp message IDs.
    Prevents duplicate processing of the same message.
    """

    def __init__(self):
        self.use_database = DB_AVAILABLE and self._check_database_connection()
        self.memory_cache = LRUCacheWithTTL(max_size=10000, ttl_seconds=86400)  # 24 hour TTL

        if self.use_database:
            logging.info("[DEDUPE] Using database for message deduplication")
            self._ensure_table_exists()
        else:
            logging.info("[DEDUPE] Using in-memory LRU cache for message deduplication (database not available)")

    def _check_database_connection(self) -> bool:
        """Check if database connection is available."""
        try:
            if engine is None:
                return False
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                return True
        except Exception as e:
            logging.warning(f"[DEDUPE] Database connection check failed: {e}")
            return False

    def _ensure_table_exists(self):
        """Ensure processed_messages table exists."""
        try:
            if engine is None or ProcessedMessage is None:
                self.use_database = False
                return
            ProcessedMessage.__table__.create(engine, checkfirst=True)
        except Exception as e:
            logging.warning(f"[DEDUPE] Could not ensure table exists: {e}")
            self.use_database = False

    def is_duplicate(self, message_id: str) -> bool:
        """
        Check if message ID has already been processed.

        Args:
            message_id: WhatsApp message ID from Meta payload

        Returns:
            True if message was already processed, False otherwise
        """
        if not message_id:
            return False

        # Check database first if available
        if self.use_database and ProcessedMessage is not None:
            try:
                session = get_db_session()
                try:
                    existing = session.query(ProcessedMessage).filter_by(
                        message_id=message_id
                    ).first()
                    if existing:
                        logging.info(f"[DEDUPE] Duplicate message detected (DB): {message_id}")
                        return True
                finally:
                    session.close()
            except Exception as e:
                logging.warning(f"[DEDUPE] Database check failed, falling back to memory cache: {e}")
                # Fall through to memory cache

        # Check memory cache
        if self.memory_cache.has(message_id):
            logging.info(f"[DEDUPE] Duplicate message detected (cache): {message_id}")
            return True

        return False

    def mark_as_processed(self, message_id: str):
        """
        Mark message ID as processed.

        Args:
            message_id: WhatsApp message ID from Meta payload
        """
        if not message_id:
            return

        # Store in database if available
        if self.use_database and ProcessedMessage is not None:
            try:
                session = get_db_session()
                try:
                    # Use INSERT ... ON CONFLICT DO NOTHING to handle race conditions
                    from sqlalchemy.dialects.postgresql import insert
                    stmt = insert(ProcessedMessage).values(
                        message_id=message_id,
                        processed_at=datetime.utcnow()
                    )
                    stmt = stmt.on_conflict_do_nothing(index_elements=['message_id'])
                    session.execute(stmt)
                    session.commit()
                finally:
                    session.close()
            except Exception as e:
                logging.warning(f"[DEDUPE] Database store failed, using memory cache: {e}")
                # Fall through to memory cache

        # Always store in memory cache as backup
        self.memory_cache.add(message_id)

    def cleanup_old_entries(self, days: int = 7):
        """
        Clean up old processed message IDs from database.

        Args:
            days: Delete entries older than this many days (default 7)
        """
        if not self.use_database or ProcessedMessage is None:
            # Cleanup memory cache instead
            cleaned = self.memory_cache.cleanup_expired()
            if cleaned > 0:
                logging.info(f"[DEDUPE] Cleaned up {cleaned} expired entries from memory cache")
            return

        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            session = get_db_session()
            try:
                deleted = session.query(ProcessedMessage).filter(
                    ProcessedMessage.processed_at < cutoff_date
                ).delete()
                session.commit()
                if deleted > 0:
                    logging.info(f"[DEDUPE] Cleaned up {deleted} old entries from database")
            finally:
                session.close()
        except Exception as e:
            logging.error(f"[DEDUPE] Error cleaning up old entries: {e}")


# Global instance
message_deduplication_service = MessageDeduplicationService()
