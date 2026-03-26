from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from app.database import Base


class Operator(Base):
    """転送先スマホ（担当者）管理"""
    __tablename__ = "operators"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)           # 担当者名
    phone_number = Column(String(20), nullable=False)   # スマホ番号 (+81...)
    priority = Column(Integer, default=1)               # 転送優先度（低い数値が高優先）
    is_active = Column(Boolean, default=True)           # 登録有効
    is_available = Column(Boolean, default=True)        # 現在空き状態か
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
