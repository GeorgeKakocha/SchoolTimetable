"""teacher first name last name

Revision ID: cae76cba3c58
Revises: 01b2ae564170
Create Date: 2026-09-09 11:16:10.054519

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cae76cba3c58'
down_revision: Union[str, Sequence[str], None] = '01b2ae564170'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Owner Decision #37: `teacher.name` (one column) becomes
    `teacher.first_name` + `teacher.last_name` (two columns). Unlike
    `01b2ae564170`'s deliberately fail-closed role migration (no safe,
    non-heuristic way to backfill an authoritative role), a first/last
    split of an *existing* single name has one safe, non-destructive,
    non-heuristic backfill: the entire old value becomes `first_name`
    and `last_name` becomes `''` -- never a guessed split on whitespace
    (a name like "Van Der Berg" would be mis-split by any heuristic).
    `Teacher.full_name` (`domain/people.py`) reconstructs the original
    value byte-for-byte from exactly this shape (empty `last_name`
    contributes no extra space), so no visible display name anywhere
    in the product changes as a result of this migration alone.

    Staged as: (1)-(2) add both columns nullable, (3) backfill scoped
    to `teacher` only, (4)-(5) tighten to `NOT NULL`, (6) drop the old
    column -- never a single destructive `ALTER` that could leave a
    partially-migrated table on failure. No `server_default` survives
    on the final columns.
    """
    # (1)-(2) Add both new columns, temporarily nullable.
    op.add_column('teacher', sa.Column('first_name', sa.Text(), nullable=True))
    op.add_column('teacher', sa.Column('last_name', sa.Text(), nullable=True))

    # (3) Backfill every existing row -- scoped to `teacher` only.
    op.execute("UPDATE teacher SET first_name = name, last_name = ''")

    # (4)-(5) Now that every row is backfilled, tighten to NOT NULL.
    op.alter_column('teacher', 'first_name', nullable=False)
    op.alter_column('teacher', 'last_name', nullable=False)

    # (6) Drop the old single-name column.
    op.drop_column('teacher', 'name')


def downgrade() -> None:
    """Downgrade schema.

    Mirrors `upgrade()` in reverse, staged the same way: (1) add the
    old `name` column back nullable, (2) rebuild it from
    `first_name`/`last_name` using the exact same whitespace-safe join
    `Teacher.full_name` uses (a blank `last_name` contributes no extra
    space, so a row that went through this migration's own upgrade
    reconstructs byte-for-byte), (3) tighten to NOT NULL, (4)-(5) drop
    the first/last columns. No data loss for the representable
    first/last model -- a genuinely two-part name loses the split (by
    design; that information has no home in the old single-column
    shape), never a value.
    """
    # (1) Add the old column back, temporarily nullable.
    op.add_column('teacher', sa.Column('name', sa.Text(), nullable=True))

    # (2) Rebuild it -- exactly `Teacher.full_name`'s own join logic:
    # trim each part, drop an empty one, join the rest with one space.
    op.execute(
        "UPDATE teacher SET name = trim("
        "  trim(first_name) || CASE WHEN trim(last_name) = '' THEN '' ELSE ' ' || trim(last_name) END"
        ")"
    )

    # (3) Tighten to NOT NULL now that every row is backfilled.
    op.alter_column('teacher', 'name', nullable=False)

    # (4)-(5) Drop the first/last columns.
    op.drop_column('teacher', 'last_name')
    op.drop_column('teacher', 'first_name')
