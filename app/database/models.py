"""
Database models for Multi-Tenant WhatsApp bot.
Includes models for businesses, users, WhatsApp numbers, customers, and conversations.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Numeric, create_engine
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()


# ============================================================================
# MULTI-TENANT MODELS
# ============================================================================

class Business(Base):
    """Model for businesses/organizations."""
    __tablename__ = 'businesses'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    business_type = Column(String(50), default='barberia')
    settings = Column(JSONB, default={})
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    phone_number_id = Column(String(255), nullable=False, unique=True, index=True)  # Meta ID or "twilio:+123" for Twilio
    phone_number = Column(String(50), nullable=False)  # E.164 number for lookup (e.g., +15556738752)
    display_name = Column(String(255), nullable=True)  # Optional friendly name
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(String(50), nullable=True, index=True)  # 'super_admin' for OmnIA team, NULL for business users
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    role = Column(String(50), default='staff')  # 'admin' for business owners/admins, 'staff' for employees
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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
    last_activity_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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


class Conversation(Base):
    """Model for storing conversation messages."""
    __tablename__ = 'conversations'

    id = Column(Integer, primary_key=True)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    whatsapp_number_id = Column(UUID(as_uuid=True), ForeignKey('whatsapp_numbers.id', ondelete='SET NULL'), nullable=True, index=True)
    whatsapp_id = Column(String(50), nullable=False, index=True)  # Customer's WhatsApp ID
    message = Column(Text, nullable=False)
    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
    agent_type = Column(String(50), nullable=True)  # Future-proofing for per-agent history
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    business = relationship("Business", back_populates="conversations")
    whatsapp_number = relationship("WhatsappNumber", back_populates="conversations")

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Order(Base):
    """Model for customer orders."""
    __tablename__ = 'orders'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id', ondelete='SET NULL'), nullable=True, index=True)
    whatsapp_id = Column(String(50), nullable=True)
    status = Column(String(20), default='pending', index=True)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)
    notes = Column(Text, nullable=True)
    delivery_address = Column(Text, nullable=True)
    contact_phone = Column(String(50), nullable=True)
    payment_method = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    order_items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': str(self.id),
            'business_id': str(self.business_id),
            'customer_id': self.customer_id,
            'whatsapp_id': self.whatsapp_id,
            'status': self.status,
            'total_amount': float(self.total_amount) if self.total_amount else 0,
            'notes': self.notes,
            'delivery_address': self.delivery_address,
            'contact_phone': self.contact_phone,
            'payment_method': self.payment_method,
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

# Create engine with connection pooling for Supabase/PostgreSQL
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,  # Max 5 persistent connections
    max_overflow=10,  # Allow 10 additional connections if pool is full
    pool_timeout=30,  # Wait 30s for connection before failing
    pool_recycle=3600,  # Recycle connections after 1 hour
    pool_pre_ping=True  # Verify connections before using them
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