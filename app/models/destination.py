from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from app.database import Base


class Destination(Base):
    """発信先（宛先）管理"""
    __tablename__ = "destinations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)           # "A", "B" など識別ラベル
    phone_number = Column(String(20), nullable=False)   # 実際の電話番号
    is_active = Column(Boolean, default=True)           # リダイアル有効/無効
    allocated_lines = Column(Integer, default=5)        # 割当回線数
    transfer_on_ringing = Column(Boolean, default=False)  # True: 呼び出し音段階でスマホへ転送
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
