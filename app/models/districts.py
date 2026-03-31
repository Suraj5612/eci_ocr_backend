from sqlalchemy import Column, Integer, String

from app.db.base import Base


class District(Base):
    __tablename__ = "districts"

    district_id = Column(Integer, primary_key=True)
    district_name_en = Column(String)
    district_name_hi = Column(String)
    mandala_id = Column(Integer)