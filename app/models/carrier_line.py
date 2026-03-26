from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class CarrierLine(Base):
    """Twilio/Telnyx の発信回線管理"""
    __tablename__ = "carrier_lines"

    id = Column(Integer, primary_key=True, index=True)
    carrier = Column(String(10), nullable=False)        # "twilio" or "telnyx"
    from_number = Column(String(20), nullable=False)    # 発信番号 (+81...)
    destination_id = Column(Integer, ForeignKey("destinations.id"), nullable=True)
    priority = Column(Integer, default=1)               # 1=高優先, 数値が大きいほど低優先
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    destination = relationship("Destination", lazy="selectin")
