"""Единственный источник данных — СПбГУ

Предыдущая миграция (a1c2e3f4b5d6) добавляла колонку `university` со
server_default='spbpu', потому что тогда все данные были из Политеха. Его
поддержка убрана, поэтому:

  • переносим существующие строки на 'spbgu';
  • меняем значение по умолчанию, чтобы новые строки не помечались вузом,
    которого больше нет.

Саму a1c2e3f4b5d6 не трогаем: она уже могла быть применена, а применённую
историю не переписывают.

Revision ID: b2d3f4a5c6e7
Revises: a1c2e3f4b5d6
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d3f4a5c6e7"
down_revision = "a1c2e3f4b5d6"
branch_labels = None
depends_on = None

_TABLES = ("programs", "applicants")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"UPDATE {table} SET university = 'spbgu' WHERE university = 'spbpu'")
        with op.batch_alter_table(table) as batch:  # SQLite умеет менять default только так
            batch.alter_column(
                "university",
                existing_type=sa.String(),
                existing_nullable=False,
                server_default="spbgu",
            )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "university",
                existing_type=sa.String(),
                existing_nullable=False,
                server_default="spbpu",
            )
