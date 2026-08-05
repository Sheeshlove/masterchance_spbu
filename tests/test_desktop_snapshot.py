"""
Тесты менеджера снапшота десктоп-клиента.

Поднимает локальный HTTP-сервер и реально прогоняет скачивание, распаковку
gzip, условный GET (304), офлайн-фолбэк на кэш и ошибку «нет ни сети, ни
кэша». Внешняя сеть не нужна.
"""
import functools
import gzip
import http.server
import socketserver
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.presentation.desktop.snapshot import (  # noqa: E402
    SnapshotManager,
    SnapshotMeta,
    SnapshotUnavailable,
    should_refresh,
)

_DEAD_URL = "http://127.0.0.1:1/nope.db.gz"


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE program_quantiles (program_code TEXT, q90 REAL, q95 REAL)")
    con.execute("INSERT INTO program_quantiles VALUES ('701', 210.0, 225.0)")
    con.commit()
    con.close()


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    httpd.allow_reuse_address = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/snap.db.gz"


def test_download_unpack_cache_and_offline():
    served = Path(tempfile.mkdtemp())
    raw = served / "snap.db"
    _make_db(raw)
    with open(raw, "rb") as src, gzip.open(served / "snap.db.gz", "wb") as dst:
        dst.write(src.read())
    raw.unlink()

    httpd, url = _serve(served)
    try:
        cache = Path(tempfile.mkdtemp())
        mgr = SnapshotManager(url, cache_dir=cache)

        # 1) скачивание + распаковка → валидный SQLite
        db = mgr.ensure()
        assert db.exists()
        con = sqlite3.connect(db)
        assert con.execute("SELECT COUNT(*) FROM program_quantiles").fetchone()[0] == 1
        con.close()

        # 2) в пределах TTL в сеть не ходим
        msgs: list[str] = []
        mgr.ensure(progress=msgs.append)
        assert any("кэш" in m for m in msgs)

        # 3) force → условный GET; сервер отдаёт 304, файл остаётся рабочим
        msgs.clear()
        mgr.ensure(force=True, progress=msgs.append)
        assert db.exists()
        assert mgr.read_meta() is not None

        # 4) сеть пропала, кэш есть → работаем на кэше
        offline = SnapshotManager(_DEAD_URL, cache_dir=cache)
        assert offline.ensure(force=True).exists()
    finally:
        httpd.shutdown()


def test_no_network_and_no_cache_raises():
    mgr = SnapshotManager(_DEAD_URL, cache_dir=Path(tempfile.mkdtemp()))
    try:
        mgr.ensure(force=True)
        raise AssertionError("ожидался SnapshotUnavailable")
    except SnapshotUnavailable:
        pass


def test_should_refresh():
    now = datetime.now(timezone.utc)
    assert should_refresh(None, now) is True
    assert should_refresh(SnapshotMeta(downloaded_at=(now - timedelta(hours=1)).isoformat()), now) is False
    assert should_refresh(SnapshotMeta(downloaded_at=(now - timedelta(hours=9)).isoformat()), now) is True
    assert should_refresh(SnapshotMeta(downloaded_at="не дата"), now) is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
