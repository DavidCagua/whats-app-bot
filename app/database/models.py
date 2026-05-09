"""
Database models for Multi-Tenant WhatsApp bot.
Includes models for businesses, users, WhatsApp numbers, customers, and conversations.
"""

from sqlalchemy import (
    Column,
    Integer,
    SmallInteger,
    String,
    Text,
    Date,
    DateTime,
    Time,
    Boolean,
    ForeignKey,
    Numeric,
    create_engine,
    BigInteger,
    Index,
    UniqueConstraint,
    MetaData,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, ENUM as PgEnum
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from pgvector.sqlalchemy import Vector
from datetime import datetime, timezone
import os
import uuid
from dotenv import load_dotenv


def _utcnow() -> datetime:
    """Timezone-aware UTC now. Used by SQLAlchemy onupdate hooks."""
    return datetime.now(timezone.utc)

load_dotenv()

# Naming convention matches the raw SQL migrations in /migrations/*.sql
# so that `index=True` and unique=True produce the same names as prod.
# - Single-column indexes: idx_<table>_<column>
# - Unique constraints:    <table>_<column>_key   (postgres default)
# - Composite indexes/constraints: declared explicitly in __table_args__.
NAMING_CONVENTION = {
    "ix": "idx_%(table_name)s_%(column_0_name)s",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}

Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))


# ============================================================================
# MULTI-TENANT MODELS
# ============================================================================

class Business(Base):
    """Model for businesses/organizations."""
    __tablename__ = 'businesses'
    __table_args__ = {"comment": "Multi-tenant businesses table - Migration 001"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    business_type = Column(String(50), default='barberia')
    settings = Column(JSONB, default={})
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    # Relationships
    whatsapp_numbers = relationship("WhatsappNumber", back_populates="business", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="business", cascade="all, delete-orphan")
    user_businesses = relationship("UserBusiness", back_populates="business", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Business(id={self.id}, name='{self.name}', type='{self.business_type}')>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'name': self.name,
            'business_type': self.business_type,
            'settings': self.settings,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def get_setting(self, key: str, default=None):
        """Get a specific setting from the JSONB settings field."""
        return self.settings.get(key, default) if self.settings else default


class WhatsappNumber(Base):
    """
    Model for WhatsApp Business API phone numbers.
    Note: All numbers share the same Meta App credentials (from .env).
    Only phone_number_id differs per business.
    """
    __tablename__ = 'whatsapp_numbers'
    __table_args__ = {"comment": "WhatsApp Business API numbers - Migration 001"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    # Nullable: businesses using Twilio (or any non-Meta provider) don't have
    # a Meta phone_number_id. Migration 005 made it optional and added a
    # partial unique index on the column where it's not null.
    phone_number_id = Column(String(255), nullable=True, index=True)
    phone_number = Column(String(50), nullable=False)  # E.164 number for lookup (e.g., +15556738752)
    display_name = Column(
        String(255),
        nullable=True,
        comment='Optional friendly name to identify this WhatsApp number (e.g., "Main Line", "Support Line")',
    )
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    # Relationships
    business = relationship("Business", back_populates="whatsapp_numbers")
    conversations = relationship("Conversation", back_populates="whatsapp_number")

    def __repr__(self):
        return f"<WhatsappNumber(id={self.id}, phone_number='{self.phone_number}', business_id={self.business_id})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'phone_number_id': self.phone_number_id,
            'phone_number': self.phone_number,
            'display_name': self.display_name,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class User(Base):
    """Model for system users who can manage businesses."""
    __tablename__ = 'users'
    __table_args__ = {"comment": "System users who can manage businesses - Migration 001"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(
        String(50),
        nullable=True,
        index=True,
        comment='User role: super_admin (full access), admin (org admin), staff (read-only)',
    )
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    # Relationships
    user_businesses = relationship("UserBusiness", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}', name='{self.full_name}', role='{self.role}')>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'email': self.email,
            'full_name': self.full_name,
            'role': self.role,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class UserBusiness(Base):
    """Model for user-business relationships (many-to-many with roles)."""
    __tablename__ = 'user_businesses'
    __table_args__ = {"comment": "User-business access control - Migration 001"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    role = Column(String(50), default='member')  # 'admin' for business owners/admins, 'member' for employees
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    # Relationships
    user = relationship("User", back_populates="user_businesses")
    business = relationship("Business", back_populates="user_businesses")

    def __repr__(self):
        return f"<UserBusiness(user_id={self.user_id}, business_id={self.business_id}, role='{self.role}')>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'user_id': str(self.user_id),
            'business_id': str(self.business_id),
            'role': self.role,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ============================================================================
# EXISTING MODELS (UPDATED FOR MULTI-TENANCY)
# ============================================================================

class AgentType(Base):
    """Reference table for available agent types."""
    __tablename__ = 'agent_types'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    def to_dict(self):
        return {
            'id': str(self.id),
            'type': self.type,
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class BusinessAgent(Base):
    """Maps which agents are enabled per business."""
    __tablename__ = 'business_agents'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    agent_type = Column(String(50), nullable=False)
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=100)
    config = Column(JSONB, default={})
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'agent_type': self.agent_type,
            'enabled': self.enabled,
            'priority': self.priority,
            'config': self.config,
            'created_by': str(self.created_by) if self.created_by else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class ConversationSession(Base):
    """Per-conversation session state for multi-turn flows."""
    __tablename__ = 'conversation_sessions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wa_id = Column(String(50), nullable=False)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False)
    active_agents = Column(JSONB, default=[])
    order_context = Column(JSONB, default={})
    booking_context = Column(JSONB, default={})
    agent_contexts = Column(JSONB, default={})
    last_order_id = Column(String(50), nullable=True)
    last_booking_id = Column(String(50), nullable=True)
    last_activity_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': str(self.id),
            'wa_id': self.wa_id,
            'business_id': str(self.business_id),
            'active_agents': self.active_agents or [],
            'order_context': self.order_context or {},
            'booking_context': self.booking_context or {},
            'agent_contexts': self.agent_contexts or {},
            'last_order_id': self.last_order_id,
            'last_booking_id': self.last_booking_id,
            'last_activity_at': self.last_activity_at.isoformat() if self.last_activity_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class ConversationAgentSetting(Base):
    """Per-conversation agent enable/disable overrides."""
    __tablename__ = 'conversation_agent_settings'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    whatsapp_id = Column(String(50), nullable=False, index=True)
    agent_enabled = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'whatsapp_id': self.whatsapp_id,
            'agent_enabled': bool(self.agent_enabled),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class ConversationAttachment(Base):
    """One attachment per media (audio, image, video, document) linked to a conversation message."""
    __tablename__ = 'conversation_attachments'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(Integer, ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    type = Column(String(20), nullable=False, index=True)  # audio, image, video, document
    content_type = Column(String(255), nullable=True)
    provider_media_url = Column(Text, nullable=True)
    provider_media_id = Column(String(255), nullable=True)
    url = Column(Text, nullable=True)  # Our Supabase URL after upload
    size_bytes = Column(BigInteger, nullable=True)
    duration_sec = Column(Numeric(10, 2), nullable=True)
    transcript = Column(Text, nullable=True)
    provider_metadata = Column(JSONB, default={}, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="attachments")

    def to_dict(self):
        return {
            'id': str(self.id),
            'conversation_id': self.conversation_id,
            'type': self.type,
            'content_type': self.content_type,
            'provider_media_url': self.provider_media_url,
            'provider_media_id': self.provider_media_id,
            'url': self.url,
            'size_bytes': self.size_bytes,
            'duration_sec': float(self.duration_sec) if self.duration_sec is not None else None,
            'transcript': self.transcript,
            'provider_metadata': self.provider_metadata or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Conversation(Base):
    """Model for storing conversation messages."""
    __tablename__ = 'conversations'

    id = Column(Integer, primary_key=True)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    whatsapp_number_id = Column(UUID(as_uuid=True), ForeignKey('whatsapp_numbers.id', ondelete='SET NULL'), nullable=True, index=True)
    whatsapp_id = Column(String(50), nullable=False, index=True)  # Customer's WhatsApp ID
    message = Column(Text, nullable=False)
    message_type = Column(String(20), default='text', nullable=True)  # text | audio | image | document
    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
    agent_type = Column(String(50), nullable=True)  # Future-proofing for per-agent history
    timestamp = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    # Relationships
    business = relationship("Business", back_populates="conversations")
    whatsapp_number = relationship("WhatsappNumber", back_populates="conversations")
    attachments = relationship("ConversationAttachment", back_populates="conversation", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Conversation(id={self.id}, business_id={self.business_id}, whatsapp_id='{self.whatsapp_id}', role='{self.role}')>"

    def to_dict(self):
        """Convert to dictionary for compatibility with existing code."""
        return {
            'id': self.id,
            'business_id': str(self.business_id) if self.business_id else None,
            'whatsapp_number_id': str(self.whatsapp_number_id) if self.whatsapp_number_id else None,
            'whatsapp_id': self.whatsapp_id,
            'message': self.message,
            'content': self.message,  # For compatibility with existing code that expects 'content'
            'role': self.role,
            'agent_type': self.agent_type,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Customer(Base):
    """
    Model for storing customer information.
    Note: Customer data is business-agnostic. A person is a person.
    Business relationships are tracked through conversations (which have business_id).
    """
    __tablename__ = 'customers'

    id = Column(Integer, primary_key=True)
    whatsapp_id = Column(String(50), nullable=False, unique=True, index=True)  # Unique - one customer record per WhatsApp ID
    name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=True)
    address = Column(Text, nullable=True)
    phone = Column(String(50), nullable=True)
    payment_method = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    def __repr__(self):
        return f"<Customer(id={self.id}, whatsapp_id='{self.whatsapp_id}', name='{self.name}')>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'whatsapp_id': self.whatsapp_id,
            'name': self.name,
            'age': self.age,
            'address': self.address,
            'phone': self.phone,
            'payment_method': self.payment_method,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class BusinessCustomer(Base):
    """
    Per-business profile for a (business, customer) pair. The global
    ``customers`` row stays as canonical identity; this row carries
    per-business overrides on name/phone/address/payment_method and
    drives the admin-console customers list (one query, indexed).
    """
    __tablename__ = 'business_customers'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False)
    customer_id = Column(Integer, ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(100), nullable=True)
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    payment_method = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    source = Column(String(20), nullable=False, default='auto', server_default='auto')
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('business_id', 'customer_id', name='uq_business_customers_pair'),
        Index('idx_business_customers_business_id', 'business_id'),
        Index('idx_business_customers_customer_id', 'customer_id'),
    )


# ============================================================================
# PRODUCTS AND ORDERS (Migration 007/008)
# ============================================================================

class Product(Base):
    """Model for products in a business catalog."""
    __tablename__ = 'products'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(10), default='COP')
    category = Column(String(50), nullable=True, index=True)
    sku = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")
    product_metadata = Column("metadata", JSONB, nullable=False, server_default="{}")
    embedding = Column(Vector(1536), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_products_business_sku", "business_id", "sku"),
        Index("idx_products_tags_gin", "tags", postgresql_using="gin"),
        Index(
            "idx_products_metadata_gin",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
        Index(
            "idx_products_embedding_cosine",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": 100},
        ),
    )

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'name': self.name,
            'description': self.description,
            'price': float(self.price) if self.price else 0,
            'currency': self.currency,
            'category': self.category,
            'sku': self.sku,
            'is_active': self.is_active,
            'tags': list(self.tags or []),
            'metadata': dict(self.product_metadata or {}),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Service(Base):
    """Model for bookable services in a business catalog."""
    __tablename__ = 'services'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(10), default='COP')
    duration_minutes = Column(Integer, nullable=False, default=60)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'name': self.name,
            'description': self.description,
            'price': float(self.price) if self.price else 0,
            'currency': self.currency,
            'duration_minutes': self.duration_minutes,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


# Postgres ENUM for orders.status. The type is owned by the alembic
# migration (a1c2d3e4f5b6); create_type=False keeps SQLAlchemy from
# trying to CREATE TYPE again on metadata.create_all in tests.
ORDER_STATUS_VALUES = (
    'pending',
    'confirmed',
    'out_for_delivery',
    'completed',
    'cancelled',
)
order_status_enum = PgEnum(
    *ORDER_STATUS_VALUES,
    name='order_status',
    create_type=False,
)


class Order(Base):
    """Model for customer orders."""
    __tablename__ = 'orders'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id', ondelete='SET NULL'), nullable=True, index=True)
    whatsapp_id = Column(String(50), nullable=True)
    status = Column(order_status_enum, nullable=False, default='pending', server_default='pending', index=True)
    fulfillment_type = Column(Text, nullable=False, default='delivery', server_default='delivery', index=True)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)
    notes = Column(Text, nullable=True)
    delivery_address = Column(Text, nullable=True)
    contact_phone = Column(Text, nullable=True)
    payment_method = Column(Text, nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    promo_discount_amount = Column(Numeric(12, 2), nullable=False, default=0, server_default='0')
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    order_items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    applied_promotions = relationship("OrderPromotion", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'customer_id': self.customer_id,
            'whatsapp_id': self.whatsapp_id,
            'status': self.status,
            'fulfillment_type': self.fulfillment_type or 'delivery',
            'total_amount': float(self.total_amount) if self.total_amount else 0,
            'notes': self.notes,
            'delivery_address': self.delivery_address,
            'contact_phone': self.contact_phone,
            'payment_method': self.payment_method,
            'cancellation_reason': self.cancellation_reason,
            'promo_discount_amount': float(self.promo_discount_amount) if self.promo_discount_amount is not None else 0,
            'confirmed_at': self.confirmed_at.isoformat() if self.confirmed_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'cancelled_at': self.cancelled_at.isoformat() if self.cancelled_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class BusinessAvailability(Base):
    """Defines open hours and slot config per business per day of week."""
    __tablename__ = 'business_availability'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    day_of_week = Column(SmallInteger, nullable=False)  # 0=Sunday, 6=Saturday
    open_time = Column(Time, nullable=False)
    close_time = Column(Time, nullable=False)
    slot_duration_minutes = Column(Integer, nullable=False, default=60)
    is_active = Column(Boolean, default=True)
    staff_member_id = Column(UUID(as_uuid=True), ForeignKey('staff_members.id', ondelete='CASCADE'), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'day_of_week': self.day_of_week,
            'open_time': self.open_time,
            'close_time': self.close_time,
            'slot_duration_minutes': self.slot_duration_minutes,
            'is_active': self.is_active,
            'staff_member_id': str(self.staff_member_id) if self.staff_member_id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Booking(Base):
    """Model for customer bookings."""
    __tablename__ = 'bookings'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id', ondelete='SET NULL'), nullable=True, index=True)
    service_name = Column(String(255), nullable=True)
    service_id = Column(UUID(as_uuid=True), ForeignKey('services.id', ondelete='SET NULL'), nullable=True, index=True)
    start_at = Column(DateTime(timezone=True), nullable=False, index=True)
    end_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), nullable=False, default='confirmed')  # pending/confirmed/cancelled/no_show/completed
    notes = Column(Text, nullable=True)
    created_via = Column(String(20), default='whatsapp')  # whatsapp/admin/api
    staff_member_id = Column(UUID(as_uuid=True), ForeignKey('staff_members.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    customer = relationship("Customer", backref="bookings")
    service = relationship("Service", backref="bookings")

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'customer_id': self.customer_id,
            'service_name': self.service_name,
            'service_id': str(self.service_id) if self.service_id else None,
            'start_at': self.start_at.isoformat() if self.start_at else None,
            'end_at': self.end_at.isoformat() if self.end_at else None,
            'status': self.status,
            'notes': self.notes,
            'created_via': self.created_via,
            'staff_member_id': str(self.staff_member_id) if self.staff_member_id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class OrderItem(Base):
    """Model for order line items."""
    __tablename__ = 'order_items'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey('orders.id', ondelete='CASCADE'), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey('products.id', ondelete='RESTRICT'), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    line_total = Column(Numeric(12, 2), nullable=False)
    notes = Column(Text, nullable=True)
    promotion_id = Column(UUID(as_uuid=True), ForeignKey('promotions.id', ondelete='SET NULL'), nullable=True, index=True)
    promo_group_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    order = relationship("Order", back_populates="order_items")
    product = relationship("Product", backref="order_items")

    def to_dict(self):
        return {
            'id': str(self.id),
            'order_id': str(self.order_id),
            'product_id': str(self.product_id),
            'quantity': self.quantity,
            'unit_price': float(self.unit_price) if self.unit_price else 0,
            'line_total': float(self.line_total) if self.line_total else 0,
            'notes': self.notes,
            'promotion_id': str(self.promotion_id) if self.promotion_id else None,
            'promo_group_id': str(self.promo_group_id) if self.promo_group_id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Promotion(Base):
    """A configurable promo rule (schedule + components + pricing mode)."""
    __tablename__ = 'promotions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default='true')

    # Pricing mode — exactly ONE non-null (DB CHECK enforces).
    fixed_price = Column(Numeric(12, 2), nullable=True)
    discount_amount = Column(Numeric(12, 2), nullable=True)
    discount_pct = Column(SmallInteger, nullable=True)

    # Schedule (NULL = no constraint on that dimension).
    days_of_week = Column(ARRAY(SmallInteger), nullable=True)  # ISO 1=Mon..7=Sun
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    # Calendar boundaries — DATE, not DATETIME. Time-of-day is handled
    # separately by start_time / end_time. Aligns with the migration
    # b2d4e6f8a0c1 which created these as `sa.Date()`.
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    components = relationship(
        "PromotionComponent",
        back_populates="promotion",
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'name': self.name,
            'description': self.description,
            'is_active': self.is_active,
            'fixed_price': float(self.fixed_price) if self.fixed_price is not None else None,
            'discount_amount': float(self.discount_amount) if self.discount_amount is not None else None,
            'discount_pct': self.discount_pct,
            'days_of_week': list(self.days_of_week) if self.days_of_week else None,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'starts_on': self.starts_on.isoformat() if self.starts_on else None,
            'ends_on': self.ends_on.isoformat() if self.ends_on else None,
            'components': [c.to_dict() for c in (self.components or [])],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class PromotionComponent(Base):
    """One required (product, qty) pair belonging to a Promotion."""
    __tablename__ = 'promotion_components'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promotion_id = Column(UUID(as_uuid=True), ForeignKey('promotions.id', ondelete='CASCADE'), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey('products.id', ondelete='CASCADE'), nullable=False)
    quantity = Column(SmallInteger, nullable=False, default=1, server_default='1')

    promotion = relationship("Promotion", back_populates="components")
    product = relationship("Product")

    def to_dict(self):
        return {
            'id': str(self.id),
            'promotion_id': str(self.promotion_id),
            'product_id': str(self.product_id),
            'quantity': int(self.quantity or 0),
        }


class OrderPromotion(Base):
    """Audit row: a promo applied to an order at place_order time."""
    __tablename__ = 'order_promotions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey('orders.id', ondelete='CASCADE'), nullable=False, index=True)
    promotion_id = Column(UUID(as_uuid=True), ForeignKey('promotions.id', ondelete='RESTRICT'), nullable=False, index=True)
    promotion_name = Column(String(120), nullable=False)
    pricing_mode = Column(String(20), nullable=False)
    discount_applied = Column(Numeric(12, 2), nullable=False)
    applied_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)

    def to_dict(self):
        return {
            'id': str(self.id),
            'order_id': str(self.order_id),
            'promotion_id': str(self.promotion_id),
            'promotion_name': self.promotion_name,
            'pricing_mode': self.pricing_mode,
            'discount_applied': float(self.discount_applied) if self.discount_applied is not None else 0,
            'applied_at': self.applied_at.isoformat() if self.applied_at else None,
        }


class StaffMember(Base):
    """Model for staff members who provide services in a business."""
    __tablename__ = 'staff_members'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    role = Column(String(100), nullable=False)  # e.g., 'barber', 'hairdresser', 'stylist'
    is_active = Column(Boolean, default=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow, nullable=False)

    # Relationships
    business = relationship("Business", backref="staff_members")
    user = relationship("User", backref="staff_members")

    def __repr__(self):
        return f"<StaffMember(id={self.id}, name='{self.name}', business_id={self.business_id}, role='{self.role}')>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'name': self.name,
            'role': self.role,
            'is_active': self.is_active,
            'user_id': str(self.user_id) if self.user_id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

# Create engine with connection pooling for Supabase/PostgreSQL.
#
# When DATABASE_URL points at Supavisor's session-mode pooler, the upstream
# Supavisor backend idle-kills sessions on the order of minutes — much
# shorter than pool_recycle=3600. With pool_pre_ping=True, every silently
# dropped connection forced a full reconnect (TCP + TLS + auth + Supavisor
# lease ≈ 800ms) on the very next checkout, making every Customer lookup
# pay that tax. Mitigations:
#   - pool_recycle=180 — proactively recycle connections before Supavisor
#     declares them idle.
#   - TCP keepalives — OS-level heartbeats keep the session alive on the
#     wire so Supavisor doesn't tear it down.
# pool_pre_ping stays on as a safety net for the rare case where a
# connection still slips through stale.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=180,
    pool_pre_ping=True,
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_tables():
    """Create all tables in the database."""
    Base.metadata.create_all(bind=engine)

def get_db_session():
    """
    Get a database session.
    IMPORTANT: Always use with context manager or manually close:

    with get_db_session() as session:
        # your code here

    OR:

    session = get_db_session()
    try:
        # your code
    finally:
        session.close()
    """
    return SessionLocal()

def get_db():
    """
    Context manager for database sessions.
    Ensures proper cleanup even if exceptions occur.

    Usage:
        from contextlib import contextmanager

        with get_db() as session:
            customer = session.query(Customer).first()
            # session automatically closed
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()