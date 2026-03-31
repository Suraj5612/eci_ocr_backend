from sqlalchemy import Column, String, Boolean, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime

from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    first_name = Column(String, nullable=False)
    middle_name = Column(String, nullable=True)
    last_name = Column(String, nullable=False)

    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    mobile = Column(String, unique=True, nullable=True)

    hashed_password = Column(String, nullable=False)

    # 🔥 NEW FIELDS
    role = Column(String, nullable=False)  # superadmin, mandal, district, constituency, booth

    mandal_id = Column(Integer, nullable=True)
    district_id = Column(Integer, nullable=True)
    constituency_id = Column(Integer, nullable=True)
    booth_id = Column(Integer, ForeignKey("booths.id"), nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)