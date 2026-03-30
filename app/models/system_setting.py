from sqlalchemy import Column, Integer, Float
from app.database import Base


class SystemSetting(Base):
    """システム設定（常に id=1 の単一行）"""
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, default=1)
    dial_interval_sec = Column(Float, default=3.0, nullable=False)  # 回線間ダイアル間隔（秒）
