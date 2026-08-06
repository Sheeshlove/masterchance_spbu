# app/presentation/desktop/snapshot.py
"""
Снапшот базы для десктоп-клиента.

Монте-Карло считается по ВСЕЙ когорте (см. RecalculateMonteCarloUseCase:
get_all_applications / get_all_applicants), поэтому посчитать шанс одного
человека на его машине нельзя без парсинга всех программ. Вместо этого клиент
скачивает готовый снапшот SQLite с уже посчитанными результатами MC
(собирается на сервере скриптом build_snapshot.py и публикуется, напр. в
GitHub Releases), кэширует его локально и работает с ним офлайн.

Свежие ЛИЧНЫЕ данные (баллы/приоритеты/согласия по коду) подтягиваются
отдельно одним запросом — см. live.py.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import ssl
import tempfile
import urllib.error
import urllib.request

from app.infrastructure.http import urlopen
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

_USER_AGENT = "MasterChance-Desktop/1.0"

# Как часто по умолчанию проверять обновление снапшота.
DEFAULT_TTL = timedelta(hours=6)


def default_cache_dir(app_name: str = "MasterChance") -> Path:
    """Каталог кэша по ОС: %LOCALAPPDATA% на Windows, ~/.local/share иначе."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / app_name
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / app_name.lower()


@dataclass
class SnapshotMeta:
    """Что мы знаем о лежащем в кэше снапшоте."""
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    downloaded_at: Optional[str] = None  # ISO-8601, UTC

    def to_dict(self) -> dict:
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "downloaded_at": self.downloaded_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SnapshotMeta":
        return cls(
            etag=d.get("etag"),
            last_modified=d.get("last_modified"),
            downloaded_at=d.get("downloaded_at"),
        )

    @property
    def downloaded_dt(self) -> Optional[datetime]:
        if not self.downloaded_at:
            return None
        try:
            return datetime.fromisoformat(self.downloaded_at)
        except ValueError:
            return None


def should_refresh(meta: Optional[SnapshotMeta], now: datetime, ttl: timedelta = DEFAULT_TTL) -> bool:
    """Пора ли идти в сеть за обновлением (чистая функция — тестируется офлайн)."""
    if meta is None:
        return True
    dt = meta.downloaded_dt
    if dt is None:
        return True
    return (now - dt) >= ttl


class SnapshotUnavailable(RuntimeError):
    """Снапшота нет ни в кэше, ни в сети."""


def _explain(exc: BaseException) -> str:
    """Человеческое объяснение вместо сырой ошибки сети."""
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or isinstance(exc, ssl.SSLError):
        return (
            "Не удалось установить защищённое соединение: на компьютере нет "
            "проверочных сертификатов. Обычно помогает обновление приложения "
            "до свежей версии."
        )
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return ("Данные ещё не опубликованы (сервер ответил «не найдено»). "
                    "Загляните позже.")
        return f"Сервер ответил ошибкой {exc.code}."
    return f"Не удалось скачать данные и нет локальной копии: {exc}"


class SnapshotManager:
    """
    Держит локальную копию снапшота и обновляет её условным GET.

    ensure() возвращает путь к .db. Если сеть недоступна, но кэш есть —
    отдаёт кэш (клиент продолжает работать офлайн).
    """

    def __init__(
        self,
        url: str,
        cache_dir: Optional[Path] = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._url = url
        self._dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self._ttl = ttl

    @property
    def db_path(self) -> Path:
        return self._dir / "snapshot.db"

    @property
    def meta_path(self) -> Path:
        return self._dir / "snapshot.meta.json"

    def read_meta(self) -> Optional[SnapshotMeta]:
        try:
            return SnapshotMeta.from_dict(json.loads(self.meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def _write_meta(self, meta: SnapshotMeta) -> None:
        self.meta_path.write_text(json.dumps(meta.to_dict(), ensure_ascii=False), encoding="utf-8")

    def ensure(
        self,
        force: bool = False,
        progress: Optional[Callable[[str], None]] = None,
    ) -> Path:
        """
        Гарантировать наличие свежего снапшота локально.

        force=True — сходить в сеть независимо от TTL.
        progress   — колбэк для статус-строки в UI.
        """
        def say(msg: str) -> None:
            if progress:
                progress(msg)

        self._dir.mkdir(parents=True, exist_ok=True)
        meta = self.read_meta()
        have_cache = self.db_path.exists()

        if have_cache and not force and not should_refresh(meta, datetime.now(timezone.utc), self._ttl):
            say("Данные из локального кэша.")
            return self.db_path

        say("Проверяем обновление данных…")
        try:
            updated = self._download(meta, say)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
            if have_cache:
                say(f"Нет связи — работаем на сохранённых данных ({type(exc).__name__}).")
                return self.db_path
            raise SnapshotUnavailable(_explain(exc)) from exc

        say("Данные обновлены." if updated else "Данные уже актуальны.")
        return self.db_path

    def _download(self, meta: Optional[SnapshotMeta], say: Callable[[str], None]) -> bool:
        """Условный GET. True — скачали новое, False — сервер ответил 304."""
        headers = {"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"}
        if meta and self.db_path.exists():
            if meta.etag:
                headers["If-None-Match"] = meta.etag
            if meta.last_modified:
                headers["If-Modified-Since"] = meta.last_modified

        req = urllib.request.Request(self._url, headers=headers)
        try:
            resp = urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code == 304:  # not modified
                self._touch_meta(meta)
                return False
            raise

        with resp:
            new_meta = SnapshotMeta(
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                downloaded_at=datetime.now(timezone.utc).isoformat(),
            )
            say("Скачиваем данные…")
            self._stream_to_db(resp)

        self._write_meta(new_meta)
        return True

    def _stream_to_db(self, resp) -> None:
        """Скачать во временный файл, при необходимости разжать gzip, атомарно заменить."""
        gzipped = self._url.endswith(".gz")
        tmp_dir = self.db_path.parent
        with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False, suffix=".part") as tmp:
            tmp_path = Path(tmp.name)
            shutil.copyfileobj(resp, tmp)

        try:
            if gzipped:
                unpacked = tmp_path.with_suffix(".db.tmp")
                with gzip.open(tmp_path, "rb") as src, open(unpacked, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                tmp_path.unlink(missing_ok=True)
                tmp_path = unpacked
            os.replace(tmp_path, self.db_path)  # атомарно
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _touch_meta(self, meta: Optional[SnapshotMeta]) -> None:
        """Обновить время проверки, чтобы не ходить в сеть на каждый запуск."""
        m = meta or SnapshotMeta()
        m.downloaded_at = datetime.now(timezone.utc).isoformat()
        self._write_meta(m)
