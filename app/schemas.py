from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ─── Destination ───────────────────────────────────────────
class DestinationBase(BaseModel):
    name: str
    phone_number: str
    is_active: bool = True
    allocated_lines: int = 5
    transfer_on_ringing: bool = False


class DestinationCreate(DestinationBase):
    pass


class DestinationUpdate(BaseModel):
    name: Optional[str] = None
    phone_number: Optional[str] = None
    is_active: Optional[bool] = None
    allocated_lines: Optional[int] = None
    transfer_on_ringing: Optional[bool] = None


class DestinationOut(DestinationBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ─── CarrierLine ───────────────────────────────────────────
class CarrierLineBase(BaseModel):
    carrier: str           # "twilio" or "telnyx"
    from_number: str
    destination_id: Optional[int] = None
    priority: int = 1
    is_active: bool = True


class CarrierLineCreate(CarrierLineBase):
    pass


class CarrierLineUpdate(BaseModel):
    carrier: Optional[str] = None
    from_number: Optional[str] = None
    destination_id: Optional[int] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class CarrierLineOut(CarrierLineBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ─── Operator ──────────────────────────────────────────────
class OperatorBase(BaseModel):
    name: str
    phone_number: str
    priority: int = 1
    is_active: bool = True


class OperatorCreate(OperatorBase):
    pass


class OperatorUpdate(BaseModel):
    name: Optional[str] = None
    phone_number: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None


class OperatorOut(OperatorBase):
    id: int
    is_available: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ─── CallLog ───────────────────────────────────────────────
class CallLogOut(BaseModel):
    id: int
    destination_id: Optional[int] = None
    carrier_line_id: Optional[int] = None
    operator_id: Optional[int] = None
    call_sid: Optional[str] = None
    carrier: Optional[str] = None
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    called_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_sec: Optional[int] = None
    result: Optional[str] = None
    cost: Optional[float] = None
    transfer_to: Optional[str] = None
    transfer_connected: Optional[int] = None

    model_config = {"from_attributes": True}


# ─── Dashboard ─────────────────────────────────────────────
class DashboardStats(BaseModel):
    total_calls_today: int
    connected_calls_today: int
    total_duration_today: int     # seconds
    estimated_cost_today: float   # USD
    active_calls: int
    engine_running: bool
    destinations: list
    operators: list
