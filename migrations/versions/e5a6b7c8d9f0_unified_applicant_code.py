"""Код поступающего единый для всех вузов

Предыдущая миграция (d4f5a6b7c8e9) разложила коды абитуриентов по вузам —
исходя из того, что каждый вуз выдаёт свои номера и они пересекаются. Это
неверно: уникальный код поступающего выдаёт суперсервис, и во всех вузах он
один и тот же. 1645144 в СПбГУ и 1645144 в ВШЭ — один человек, а разложенные
по вузам ключи превращали его в шестерых.

Поэтому:

  • снимаем префикс вуза с кодов абитуриентов везде, где он проставлен;
  • строки, которые от этого совпали (один человек, найденный в двух вузах),
    схлопываем — иначе вставка упрётся в первичный ключ;
  • диагностику Монте-Карло пересобираем с ключом (абитуриент, вуз): она
    считается внутри конкурса, и у одного человека их теперь несколько. Без
    вуза в ключе прогон одного вуза затирал бы «пролетел с магой» для другого,
    а вставка второй строки падала бы на первичном ключе.

Делится по вузам не абитуриент, а конкурс: вуз зашит в код программы.

Revision ID: e5a6b7c8d9f0
Revises: d4f5a6b7c8e9
"""
from alembic import op

revision = "e5a6b7c8d9f0"
down_revision = "d4f5a6b7c8e9"
branch_labels = None
depends_on = None

#: Вузы, чьи префиксы могли попасть в код абитуриента.
_PREFIXES = ("spbgu", "hse", "itmo", "mgimo", "msu", "ranepa")

#: Таблица → колонка с кодом абитуриента → является ли код ключом целиком.
#:
#: Разница существенная. В `applicants` ключ — сам код, поэтому две строки,
#: совпавшие после снятия префикса, это один человек, и лишнюю надо убрать. А в
#: `applications` и `admission_probabilities` ключ составной, и две строки
#: одного человека по разным программам — норма: удалять их нельзя, иначе
#: заявки в один из вузов просто пропадут.
_COLUMNS = (
    ("applicants", "id", True),
    ("applications", "applicant_id", False),
    ("admission_probabilities", "applicant_id", False),
)

_DIAGNOSTICS_DDL = """
CREATE TABLE admission_diagnostics_new (
    applicant_id VARCHAR NOT NULL,
    university VARCHAR NOT NULL DEFAULT 'spbgu',
    p_excluded FLOAT NOT NULL,
    p_fail_when_included FLOAT NOT NULL,
    PRIMARY KEY (applicant_id, university),
    FOREIGN KEY(applicant_id) REFERENCES applicants (id)
)
"""


def _bare(column: str, prefix: str) -> str:
    """SQL-выражение «код без префикса вуза»."""
    return f"substr({column}, {len(prefix) + 2})"


def _strip(table: str, column: str, prefix: str, code_is_the_key: bool) -> None:
    """Снять один префикс, не создавая дублей по первичному ключу."""
    bare = _bare(column, prefix)
    if code_is_the_key:
        # Строки, которые после снятия префикса столкнутся с уже существующими,
        # — это один и тот же человек, найденный в разных вузах. Лишнюю убираем.
        op.execute(
            f"DELETE FROM {table} WHERE {column} LIKE '{prefix}:%' "
            f"AND {bare} IN (SELECT {column} FROM {table} WHERE {column} NOT LIKE '%:%')"
        )
    # OR REPLACE — страховка на настоящие дубли (одна программа, один человек,
    # две строки). Там, где ключ составной, столкнуться могут только они.
    op.execute(f"UPDATE OR REPLACE {table} SET {column} = {bare} WHERE {column} LIKE '{prefix}:%'")


def upgrade() -> None:
    # 1. Диагностика: новый ключ (абитуриент, вуз). В SQLite первичный ключ
    #    меняется только пересозданием таблицы, поэтому переливаем.
    op.execute("DROP TABLE IF EXISTS admission_diagnostics_new")
    op.execute(_DIAGNOSTICS_DDL)
    for prefix in _PREFIXES:
        op.execute(
            "INSERT OR REPLACE INTO admission_diagnostics_new "
            "(applicant_id, university, p_excluded, p_fail_when_included) "
            f"SELECT {_bare('applicant_id', prefix)}, '{prefix}', p_excluded, p_fail_when_included "
            f"FROM admission_diagnostics WHERE applicant_id LIKE '{prefix}:%'"
        )
    # Строки без префикса могли остаться от снапшотов, собранных до разделения
    # по вузам: тогда источник был один — СПбГУ.
    op.execute(
        "INSERT OR REPLACE INTO admission_diagnostics_new "
        "(applicant_id, university, p_excluded, p_fail_when_included) "
        "SELECT applicant_id, 'spbgu', p_excluded, p_fail_when_included "
        "FROM admission_diagnostics WHERE applicant_id NOT LIKE '%:%'"
    )
    op.execute("DROP TABLE admission_diagnostics")
    op.execute("ALTER TABLE admission_diagnostics_new RENAME TO admission_diagnostics")

    # 2. Снимаем префикс вуза с кодов абитуриентов.
    for table, column, code_is_the_key in _COLUMNS:
        for prefix in _PREFIXES:
            _strip(table, column, prefix, code_is_the_key)


def downgrade() -> None:
    # Обратно раскладывать людей по вузам нечем: сведений о том, из какого вуза
    # пришла строка, в кодах уже нет. Возвращаем только прежнюю форму таблицы —
    # по строке на абитуриента.
    op.execute("DROP TABLE IF EXISTS admission_diagnostics_old")
    op.execute("""
        CREATE TABLE admission_diagnostics_old (
            applicant_id VARCHAR NOT NULL,
            p_excluded FLOAT NOT NULL,
            p_fail_when_included FLOAT NOT NULL,
            PRIMARY KEY (applicant_id),
            FOREIGN KEY(applicant_id) REFERENCES applicants (id)
        )
    """)
    op.execute(
        "INSERT OR REPLACE INTO admission_diagnostics_old "
        "SELECT applicant_id, p_excluded, p_fail_when_included FROM admission_diagnostics"
    )
    op.execute("DROP TABLE admission_diagnostics")
    op.execute("ALTER TABLE admission_diagnostics_old RENAME TO admission_diagnostics")
