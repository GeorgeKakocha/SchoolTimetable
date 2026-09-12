"""drop erroneous one published configuration revision index

Revision ID: 398b05641152
Revises: 83434054f9d2
Create Date: 2026-09-12 09:34:35.905540

Corrects an architectural error in 83434054f9d2: that migration created
a partial unique index enforcing at most one PUBLISHED
`ConfigurationRevision` per `AcademicYear`. That is wrong -- `PUBLISHED`
means "this revision was finalized and is permanently immutable", NOT
"this is the one currently-active published revision". A year must be
able to accumulate any number of historical PUBLISHED revisions over
its lifetime (each one finalized by a successful Generate/regeneration
in turn); `AcademicYear.published_revision_id` alone identifies which
PUBLISHED revision is currently authoritative, and moving that pointer
to a newer PUBLISHED revision must never require -- or imply -- that an
older PUBLISHED revision stops being PUBLISHED.

This migration only drops the erroneous index. The `uq_configuration_
revision_one_draft` partial unique index (at most one DRAFT per year)
remains correct and is untouched. No data is backfilled or otherwise
changed -- every existing row's `status` is already valid under the
corrected invariant (each year currently has at most one PUBLISHED
revision anyway; this migration only removes a constraint that would
have wrongly blocked a second one from ever being created).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '398b05641152'
down_revision: Union[str, Sequence[str], None] = '83434054f9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(
        'uq_configuration_revision_one_published', table_name='configuration_revision',
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index(
        'uq_configuration_revision_one_published', 'configuration_revision', ['academic_year_id'],
        unique=True, postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
