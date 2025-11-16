from sqlalchemy import Column, Integer, String, Boolean, Text
from .base import Base

class Challenge(Base):
    __tablename__ = "challenges"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    level = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default='true')
