"""add transfer on ringing to destination and conference to call log

Revision ID: 0002_add_ringing_transfer
Revises: 0001_initial
Create Date: 2026-03-23 21:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_add_ringing_transfer'
down_revision: Union[str, None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # destinations table
    op.add_column('destinations', sa.Column('transfer_on_ringing', sa.Boolean(), server_default='0', nullable=False))
    
    # call_logs table
    op.add_column('call_logs', sa.Column('conference_name', sa.String(length=64), nullable=True))
    op.add_column('call_logs', sa.Column('operator_call_sid', sa.String(length=64), nullable=True))


def downgrade() -> None:
    # call_logs table
    op.drop_column('call_logs', 'operator_call_sid')
    op.drop_column('call_logs', 'conference_name')
    
    # destinations table
    op.drop_column('destinations', 'transfer_on_ringing')
