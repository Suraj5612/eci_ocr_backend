from sqlalchemy import Column, String, DateTime, ForeignKey
from app.db.base import Base
from datetime import datetime, timezone
import uuid


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    image_path = Column(String, nullable=False)

    status = Column(String, default="pending")  # pending, processing, completed, failed
    error_message = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))