"""add system_settings table

Revision ID: 0003_add_system_settings
Revises: 0002_add_ringing_transfer
Create Date: 2026-03-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003_add_system_settings'
down_revision: Union[str, None] = '0002_add_ringing_transfer'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'system_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dial_interval_sec', sa.Float(), nullable=False, server_default='3.0'),
    )
    # デフォルト値で初期行を挿入
    op.execute("INSERT INTO system_settings (id, dial_interval_sec) VALUES (1, 3.0)")


def downgrade() -> None:
    op.drop_table('system_settings')
