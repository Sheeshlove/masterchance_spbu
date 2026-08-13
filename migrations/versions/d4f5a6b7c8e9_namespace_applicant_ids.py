"""Код абитуриента хранится вместе с вузом

Источников стало шесть, а код поступающего каждый вуз выдаёт свой — коды
пересекаются. В базе `applicants.id` вуза не содержал, поэтому абитуриент
1645144 из СПбГУ и абитуриент 1645144 из МГУ склеились бы в одну строку, а
Монте-Карло посчитал бы их одним человеком, который может занять место только
где-то в одном месте, и обоим занизил бы шанс.

Миграция переводит уже собранные строки на новый вид ключа (`spbgu:1645144`).
Обновление списков делает то же самое для новых данных, поэтому без миграции
база какое-то время жила бы с двумя видами ключей сразу.

Правим все таблицы, где лежит код абитуриента, — включая результаты
Монте-Карло: иначе прогноз перестал бы находиться по новому ключу.

Revision ID: d4f5a6b7c8e9
Revises: c3e4f5a6b7d8
"""
from alembic import op

revision = "d4f5a6b7c8e9"
down_revision = "c3e4f5a6b7d8"
branch_labels = None
depends_on = None

#: Таблица → колонка с кодом абитуриента.
_COLUMNS = (
    ("applicants", "id"),
    ("applications", "applicant_id"),
    ("admission_probabilities", "applicant_id"),
    ("admission_diagnostics", "applicant_id"),
)

#: Единственный вуз, чьи данные могли быть собраны до этой миграции.
_UNIVERSITY = "spbgu"


def upgrade() -> None:
    # Условие «нет двоеточия» бережёт от повторного запуска и не трогает уже
    # неймспейснутые строки: коды поступающих двоеточий не содержат.
    for table, column in _COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = '{_UNIVERSITY}:' || {column} "
            f"WHERE {column} NOT LIKE '%:%'"
        )


def downgrade() -> None:
    prefix_len = len(_UNIVERSITY) + 2  # substr в SQLite считает с единицы
    for table, column in _COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = substr({column}, {prefix_len}) "
            f"WHERE {column} LIKE '{_UNIVERSITY}:%'"
        )
