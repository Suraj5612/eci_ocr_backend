from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from datetime import datetime
from app.db.base import Base


class Booth(Base):
    __tablename__ = "booths"

    id = Column(Integer, primary_key=True, index=True)
    booth_name = Column(String, nullable=False)

    constituency_id = Column(Integer, ForeignKey("constituency.id"), nullable=False)

    district_id = Column(Integer, nullable=True)
    mandal_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)