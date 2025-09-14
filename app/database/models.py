"""
Database models for WhatsApp bot conversation storage.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()

class Conversation(Base):
    """Model for storing conversation messages."""
    __tablename__ = 'conversations'

    id = Column(Integer, primary_key=True)
    whatsapp_id = Column(String(50), nullable=False, index=True)
    message = Column(Text, nullable=False)
    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Conversation(id={self.id}, whatsapp_id='{self.whatsapp_id}', role='{self.role}')>"

    def to_dict(self):
        """Convert to dictionary for compatibility with existing code."""
        return {
            'id': self.id,
            'whatsapp_id': self.whatsapp_id,
            'message': self.message,
            'content': self.message,  # For compatibility with existing code that expects 'content'
            'role': self.role,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class Customer(Base):
    """Model for storing customer information."""
    __tablename__ = 'customers'

    id = Column(Integer, primary_key=True)
    whatsapp_id = Column(String(50), nullable=False, unique=True, index=True)
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

# Create engine and session factory
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_tables():
    """Create all tables in the database."""
    Base.metadata.create_all(bind=engine)

def get_db_session():
    """Get a database session."""
    return SessionLocal()