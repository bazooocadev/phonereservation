from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class CallLog(Base):
    """通話ログ"""
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    destination_id = Column(Integer, ForeignKey("destinations.id"), nullable=True)
    carrier_line_id = Column(Integer, ForeignKey("carrier_lines.id"), nullable=True)
    operator_id = Column(Integer, ForeignKey("operators.id"), nullable=True)

    # 発信・通話情報
    call_sid = Column(String(64), nullable=True, index=True)   # Twilio/Telnyx コールID
    carrier = Column(String(10), nullable=True)                # twilio / telnyx
    from_number = Column(String(20), nullable=True)
    to_number = Column(String(20), nullable=True)

    # タイムスタンプ
    called_at = Column(DateTime(timezone=True), server_default=func.now())
    connected_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # 結果
    duration_sec = Column(Integer, nullable=True)
    result = Column(String(20), nullable=True)   # connected/busy/congested/failed/no-answer
    cost = Column(Float, nullable=True)          # USD

    # 転送先
    transfer_to = Column(String(20), nullable=True)
    transfer_connected = Column(Integer, default=0)    # 0: not yet, 1: success, 2: failed

    # コンファレンス（呼び出し音転送）
    conference_name = Column(String(64), nullable=True)   # Twilioコンファレンスルーム名
    operator_call_sid = Column(String(64), nullable=True) # スマホ側のコールSID

    destination = relationship("Destination", lazy="selectin")
    carrier_line = relationship("CarrierLine", lazy="selectin")
    operator = relationship("Operator", lazy="selectin")
