"""Alembic script template"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "destinations",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("phone_number", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("allocated_lines", sa.Integer(), default=5),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    op.create_table(
        "carrier_lines",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("carrier", sa.String(10), nullable=False),
        sa.Column("from_number", sa.String(20), nullable=False),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("destinations.id"), nullable=True),
        sa.Column("priority", sa.Integer(), default=1),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    op.create_table(
        "operators",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("phone_number", sa.String(20), nullable=False),
        sa.Column("priority", sa.Integer(), default=1),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("is_available", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    op.create_table(
        "call_logs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("destinations.id"), nullable=True),
        sa.Column("carrier_line_id", sa.Integer(), sa.ForeignKey("carrier_lines.id"), nullable=True),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=True),
        sa.Column("call_sid", sa.String(64), nullable=True, index=True),
        sa.Column("carrier", sa.String(10), nullable=True),
        sa.Column("from_number", sa.String(20), nullable=True),
        sa.Column("to_number", sa.String(20), nullable=True),
        sa.Column("called_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(20), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("transfer_to", sa.String(20), nullable=True),
        sa.Column("transfer_connected", sa.Integer(), default=0),
    )


def downgrade() -> None:
    op.drop_table("call_logs")
    op.drop_table("operators")
    op.drop_table("carrier_lines")
    op.drop_table("destinations")
