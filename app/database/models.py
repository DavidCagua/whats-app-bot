"""
Database models for Multi-Tenant WhatsApp bot.
Includes models for businesses, users, WhatsApp numbers, customers, and conversations.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, create_engine
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
    phone_number_id = Column(String(255), nullable=False, unique=True, index=True)  # Meta's phone number ID for routing
    phone_number = Column(String(50), nullable=False)  # Display number (e.g., +15556738752)
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
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user_businesses = relationship("UserBusiness", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}', name='{self.full_name}')>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'email': self.email,
            'full_name': self.full_name,
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
    role = Column(String(50), default='staff')  # 'owner', 'admin', 'staff'
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

class Conversation(Base):
    """Model for storing conversation messages."""
    __tablename__ = 'conversations'

    id = Column(Integer, primary_key=True)
    business_id = Column(UUID(as_uuid=True), ForeignKey('businesses.id', ondelete='CASCADE'), nullable=False, index=True)
    whatsapp_number_id = Column(UUID(as_uuid=True), ForeignKey('whatsapp_numbers.id', ondelete='SET NULL'), nullable=True, index=True)
    whatsapp_id = Column(String(50), nullable=False, index=True)  # Customer's WhatsApp ID
    message = Column(Text, nullable=False)
    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

# Create engine with connection pooling configuration
# Optimized for Supabase Direct Connection (not pooler)
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,                    # Maximum number of permanent connections
    max_overflow=10,                # Maximum overflow connections beyond pool_size
    pool_timeout=30,                # Seconds to wait before giving up on getting a connection
    pool_recycle=3600,              # Recycle connections after 1 hour (prevents stale connections)
    pool_pre_ping=True,             # Verify connections before using (prevents "server closed connection" errors)
    connect_args={
        "connect_timeout": 10,       # Connection timeout in seconds
        "application_name": "whatsapp_bot_flask",  # Helps identify connections in Supabase dashboard
    }
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_tables():
    """Create all tables in the database."""
    Base.metadata.create_all(bind=engine)

def get_db_session():
    """Get a database session."""
    return SessionLocal()