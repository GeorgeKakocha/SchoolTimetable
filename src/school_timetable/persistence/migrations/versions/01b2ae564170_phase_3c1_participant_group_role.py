"""phase 3c1 participant group role

Revision ID: 01b2ae564170
Revises: 4681f7a362bd
Create Date: 2026-09-07 18:30:55.299221

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '01b2ae564170'
down_revision: Union[str, Sequence[str], None] = '4681f7a362bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Deliberately fail-closed (`DECISIONS.md` #33): NOT NULL, no
    `server_default`, no backfill logic. This migration will raise
    against any `participant_group` table that already has rows --
    there is no safe, non-heuristic way for a generic migration to
    assign an authoritative WHOLE_CLASS/SUBGROUP/MERGED_CLASSES role to
    a row it knows nothing about. Any environment with existing
    unclassified rows requires its own explicit, external data-handling
    step before this migration can be applied -- this migration itself
    intentionally does not infer or backfill roles, and never will.
    Disposable/development data may simply be reset and reseeded from
    role-aware fixture/config code (see `PROJECT_STATE.md` Phase 3C.1
    for how the local synthetic dev database was handled); that is one
    environment-specific procedure, not a universal prescription this
    migration makes for every environment. A CHECK constraint is added
    by hand -- Alembic's autogenerate does not detect CHECK constraints
    reliably.
    """
    op.add_column('participant_group', sa.Column('role', sa.Text(), nullable=False))
    op.create_check_constraint(
        'ck_participant_group_role',
        'participant_group',
        "role IN ('WHOLE_CLASS', 'SUBGROUP', 'MERGED_CLASSES')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_participant_group_role', 'participant_group', type_='check')
    op.drop_column('participant_group', 'role')
