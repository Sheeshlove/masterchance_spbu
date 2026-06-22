"""add university column to programs and applicants

Revision ID: a1c2e3f4b5d6
Revises: 9a1b2c3d4e5f
Create Date: 2026-06-21 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, Sequence[str], None] = '9a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default='spbpu' backfill-ит существующие строки (это данные СПбПУ)
    op.add_column(
        'programs',
        sa.Column('university', sa.String(), nullable=False, server_default='spbpu'),
    )
    op.create_index('ix_programs_university', 'programs', ['university'])

    op.add_column(
        'applicants',
        sa.Column('university', sa.String(), nullable=False, server_default='spbpu'),
    )
    op.create_index('ix_applicants_university', 'applicants', ['university'])


def downgrade() -> None:
    op.drop_index('ix_applicants_university', table_name='applicants')
    op.drop_column('applicants', 'university')

    op.drop_index('ix_programs_university', table_name='programs')
    op.drop_column('programs', 'university')
