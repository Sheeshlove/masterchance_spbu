"""Индексы для выборок по абитуриенту

Первичный ключ `applications` — составной (program_code, applicant_id), и для
самого частого запроса сервиса — «все заявки этого абитуриента» — он бесполезен:
нужная колонка в нём вторая. SQLite умеет спасаться skip-scan'ом, но только при
наличии статистики, а ANALYZE до этого не вызывался нигде. В проде это означало
полный скан таблицы на каждый показ прогноза, и не один:

  • get_program_codes_by_applicant  — WHERE applicant_id = ?
  • get_applications_by_applicant   — WHERE applicant_id = ?
  • справочник согласий             — SELECT DISTINCT applicant_id
                                      WHERE consent IS TRUE

Замер на копии схемы (75 000 заявок): `WHERE applicant_id = ?` без статистики
даёт SCAN и 3,89 мс, с этим индексом — SEARCH и 0,01 мс. Скан по согласиям —
14,5 мс против 7,8 мс на покрывающем индексе.

Те же индексы досоздаются на старте в app/infrastructure/db/engine.py: боевой
контейнер поднимается через create_all(), а он к существующей таблице ничего
не добавляет, и alembic в docker-compose не участвует. Миграция нужна для тех,
кто ведёт схему через неё.

Revision ID: c3e4f5a6b7d8
Revises: b2d3f4a5c6e7
"""
from alembic import op

revision = "c3e4f5a6b7d8"
down_revision = "b2d3f4a5c6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS, потому что engine.ensure_indexes() мог создать их раньше:
    # база живёт в бою и без миграций тоже.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_applications_applicant "
        "ON applications (applicant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_applications_consent "
        "ON applications (consent, applicant_id)"
    )
    # Индексы без статистики планировщик может и не выбрать.
    op.execute("ANALYZE")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_applications_consent")
    op.execute("DROP INDEX IF EXISTS ix_applications_applicant")
