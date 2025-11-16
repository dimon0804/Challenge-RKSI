from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey
from .base import Base

class Endpoint(Base):
    __tablename__ = "endpoints"
    id = Column(Integer, primary_key=True)
    challenge_id = Column(Integer, ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False)
    path = Column(String(200), nullable=False)
    method = Column(String(10), nullable=False)
    prompt = Column(Text, nullable=False)
    answer_hash = Column(String(255), nullable=False)
    order_index = Column(Integer, nullable=False, server_default='0')
    is_active = Column(Boolean, nullable=False, server_default='true')
