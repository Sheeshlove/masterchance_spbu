"""
Единая точка создания подключения к БД.

Зачем отдельный модуль: `create_engine` вызывался в шести местах (сайт, бот,
обновлятор, разовые скрипты), и каждое подключение получалось с настройками
SQLite по умолчанию. А по умолчанию SQLite работает в режиме `journal=delete`,
где ПИШУЩИЙ ЗАПИРАЕТ ФАЙЛ ЦЕЛИКОМ: пока обновлятор заливает списки и
результаты Монте-Карло, сайт и бот не могут прочитать вообще ничего. Ждут они
ровно 5 секунд (столько ждёт pysqlite по умолчанию), после чего запрос падает
с `database is locked` — сайт отдаёт 500, бот отвечает «Произошла ошибка».
Раз в 3 часа сервис просто пропадал на всё время обновления.

Замер на копии схемы (75 000 заявок), читатель во время записи:

    journal=delete   медиана 4448 мс, p90 5008 мс, часть запросов — ошибка
    journal=WAL      медиана    8,8 мс, p90   11 мс, ошибок нет

Отсюда три PRAGMA ниже. Ставятся они на КАЖДОЕ соединение, потому что
`busy_timeout` и `synchronous` — свойства соединения, а не файла (в отличие от
`journal_mode`, который записан в саму базу и переживает перезапуск).
"""
from __future__ import annotations

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

# Индексы, без которых каждый показ прогноза читает таблицу заявок целиком.
#
# Первичный ключ `applications` — составной (program_code, applicant_id), и для
# `WHERE applicant_id = ?` он бесполезен: нужная колонка в нём вторая. SQLite
# умеет выкручиваться skip-scan'ом, но только если есть статистика — а ANALYZE
# в проекте никто не звал, поэтому в проде это был честный полный скан.
#
# Держим их здесь, а не только в models.py и alembic, по простой причине:
# контейнеры запускаются через `Base.metadata.create_all()`, а он для уже
# существующей таблицы не создаёт ничего, включая новые индексы. Миграция же
# в docker-compose не участвует вовсе. `IF NOT EXISTS` делает вызов дешёвым
# no-op'ом, когда индексы уже на месте.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_applications_applicant "
    "ON applications (applicant_id)",
    # Покрывающий: справочник «кто подал согласие хоть где-то» строится
    # запросом SELECT DISTINCT applicant_id WHERE consent IS TRUE, и без
    # индекса это был полный скан на каждый запрос пользователя.
    "CREATE INDEX IF NOT EXISTS ix_applications_consent "
    "ON applications (consent, applicant_id)",
)


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Engine с настройками SQLite, при которых читатели не ждут писателя."""
    engine = create_engine(url, echo=echo, future=True)

    if not url.startswith("sqlite"):
        return engine

    @event.listens_for(engine, "connect")
    def _tune(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            # Порядок важен: сначала таймаут, потом смена журнала. Переключение
            # в WAL само требует короткой блокировки, и если её занял
            # обновлятор, ждать этого мы должны уже по-новому, а не 5 секунд.
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA journal_mode=WAL")
            # При WAL полная синхронизация на каждую транзакцию не нужна:
            # потерять можно только последние транзакции при отключении
            # питания, но не саму базу. Списки всё равно перекачиваются
            # каждые 3 часа, так что цена такой потери — ноль.
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


def ensure_indexes(engine: Engine) -> None:
    """
    Досоздать индексы, которых нет. Идемпотентно и быстро.

    Вызывается на старте сайта, бота и обновлятора: на пустой базе индексы
    появятся вместе с таблицами, а на уже работающей — этим вызовом.
    """
    with engine.begin() as conn:
        for ddl in _INDEXES:
            conn.execute(text(ddl))


def analyze(engine: Engine) -> None:
    """
    Обновить статистику планировщика.

    Без sqlite_stat1 SQLite выбирает план вслепую и, в частности, не пробует
    skip-scan по составному первичному ключу. Зовём в конце прохода
    обновлятора — там данные только что поменялись целиком, и это единственный
    момент, когда статистика реально устарела.
    """
    with engine.begin() as conn:
        conn.execute(text("ANALYZE"))
