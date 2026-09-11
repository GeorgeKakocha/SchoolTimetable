"""add period bell times

Revision ID: e0f73eda567b
Revises: 9fbec2126831
Create Date: 2026-09-11 06:56:06.961901

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e0f73eda567b'
down_revision: Union[str, Sequence[str], None] = '9fbec2126831'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Calendar A: two nullable bell-clock metadata columns on `period`.
    Display-only -- the solver/preflight/verifier never read them.
    Existing rows survive with both NULL (never both-or-neither enforced
    at the DB level; that pairing rule is an application-layer
    invariant, `calendar_rules`)."""
    op.add_column('period', sa.Column('start_time', sa.Time(), nullable=True))
    op.add_column('period', sa.Column('end_time', sa.Time(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('period', 'end_time')
    op.drop_column('period', 'start_time')
