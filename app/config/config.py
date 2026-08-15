# app/config/config.py
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        populate_by_name=True,
        extra="ignore",
    )

    # окружение
    env: str = Field("dev", alias="ENV")
    data_dir: Path = Field(_DEFAULT_DATA_DIR, alias="DATA_DIR")

    # таймзона
    timezone_name: str = Field("UTC", alias="TIMEZONE")

    bot_token: str = Field("BOT_TOKEN", alias="BOT_TOKEN")

    # Сколько списков тянуть параллельно. Это обычные HTTP-запросы к отчёту
    # СПбГУ (браузер не нужен), поэтому упирается не в память, а в вежливость
    # к серверу вуза.
    parser_parallelism: int = Field(4, alias="PARSER_PARALLELISM")

    # ───────────────── Источники данных: вузы ─────────────────────────
    # Какие вузы обновлять. 'all' — все поддерживаемые (СПбГУ, ВШЭ, ИТМО,
    # МГИМО, МГУ, РАНХиГС); можно перечислить через запятую, если нужен один.
    universities: str = Field("all", alias="UNIVERSITIES")
    # Вуз по умолчанию для режимов, работающих с одним источником.
    university: str = Field("spbgu", alias="UNIVERSITY")

    # Отчёт «Списки подавших заявление» магистратуры СПбГУ (server-rendered,
    # содержит встроенный reportMeta JSON со справочником программ). Данные
    # абитуриентов подтягиваются POST-запросом к /api/reports/priem-list-02/data.
    spbgu_base_url: str = Field(
        "https://enrollelists.spbu.ru/reports/PriemList02.php", alias="SPBGU_BASE_URL"
    )

    # Стартовые страницы приёмных кампаний остальных пяти вузов. Пустое
    # значение = «взять адрес по умолчанию» (openlists/specs.py). Разделы приёма
    # вузы переносят почти каждый сезон, поэтому адрес меняется здесь, а не в
    # коде: проверить — scripts/diagnose_source.py <вуз>.
    hse_lists_url: str = Field("", alias="HSE_LISTS_URL")
    itmo_lists_url: str = Field("", alias="ITMO_LISTS_URL")
    mgimo_lists_url: str = Field("", alias="MGIMO_LISTS_URL")
    msu_lists_url: str = Field("", alias="MSU_LISTS_URL")
    ranepa_lists_url: str = Field("", alias="RANEPA_LISTS_URL")

    # Предохранитель на обход: сколько списков максимум брать с одного вуза.
    max_lists_per_source: int = Field(400, alias="MAX_LISTS_PER_SOURCE")

    # БД
    db_url: str | None = Field(None, alias="DATABASE_URL")
    db_filename: str = Field("master.db", alias="DB_FILENAME")
    db_echo: bool = Field(False, alias="DB_ECHO")

    # Веб-интерфейс ("посмотри свои шансы")
    web_host: str = Field("0.0.0.0", alias="WEB_HOST")
    web_port: int = Field(8080, alias="WEB_PORT")

    # Публичный адрес сайта для Telegram Mini App.
    # Telegram открывает Mini App только по HTTPS с валидным сертификатом —
    # http:// и голый IP он молча не примет. Пока адрес не задан, бот работает
    # как раньше, просто без кнопки «Открыть приложение».
    webapp_url: str | None = Field(None, alias="WEBAPP_URL")

    # Десктоп-клиент: откуда качать снапшот БД с посчитанным Monte-Carlo
    # (собирается build_snapshot.py и публикуется скриптом scripts/publish_snapshot.sh).
    #
    # Ссылка намеренно указывает на ФИКСИРОВАННЫЙ тег `data`, а не на
    # /releases/latest/: «latest» переезжает на любой новый релиз, поэтому
    # публикация версии с .exe сломала бы адрес снапшота у всех клиентов.
    snapshot_url: str = Field(
        "https://github.com/Sheeshlove/masterchance_spbu/releases/download/data/master-snapshot.db.gz",
        alias="SNAPSHOT_URL",
    )

    # ───────────────── Monte-Carlo: «отток» сильных ───────────────────
    # Включение механики оттока (True — включено, False — legacy-поведение).
    opt_out_enabled: bool = Field(True, alias="MC_OPTOUT_ENABLED")
    # Доля выбывающих среди тех, у кого НИГДЕ нет согласия (consent=False по всем его заявлениям).
    # Значение 0.2 означает «ровно 20% из пула E будем исключать в каждой симуляции».
    opt_out_ratio: float = Field(0.20, alias="MC_OPTOUT_RATIO")
    # Крутизна зависимости шанса «уйти» от перцентиля конкурсного балла.
    # Вероятности пропорциональны p^alpha, где p — перцентиль, alpha>=1 (3 — агрессивнее).
    opt_out_alpha: float = Field(1.0, alias="MC_OPTOUT_STRENGTH")
    # Вероятность ухода для тех, кто уже подал согласие в ДРУГОЙ вуз. Здесь мы
    # не гадаем: согласие единовременно можно держать только одно, и человек им
    # уже распорядился. Не 1.0 потому, что согласие отзывают и переподают —
    # до конца кампании он может вернуться.
    opt_out_committed: float = Field(0.9, alias="MC_CONSENT_ELSEWHERE_LEAVE")

    bot_show_anchored: bool = Field(True, alias="BOT_SHOW_ANCHORED")

    @model_validator(mode="before")
    def _preprocess(cls, values: dict[str, Any]) -> dict[str, Any]:
        raw = values.get("data_dir", _DEFAULT_DATA_DIR)
        values["data_dir"] = Path(raw).expanduser().resolve()
        return values

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def enabled_universities(self) -> tuple[str, ...]:
        """Вузы из UNIVERSITIES — уже нормализованные и без неизвестных ключей."""
        from app.domain.universities import parse_university_list

        return parse_university_list(self.universities)

    def lists_url(self, university: str) -> str:
        """Заданный в .env адрес списков вуза либо '' (тогда берётся дефолтный)."""
        return (getattr(self, f"{university}_lists_url", "") or "").strip()

    @property
    def webapp_ready(self) -> bool:
        """
        Годится ли настроенный адрес для Mini App.

        Telegram принимает только https:// — если отдать ему http, кнопка
        просто не создастся, а бот упадёт с BadRequest при старте. Лучше
        промолчать и остаться обычным ботом.
        """
        return bool(self.webapp_url and self.webapp_url.startswith("https://"))

    @property
    def database_url(self) -> str:
        return self.db_url or f"sqlite:///{self.data_dir / self.db_filename}"


settings = Settings()
