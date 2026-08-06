"""
Тесты выбора корневых сертификатов и объяснения сетевых ошибок.

Регрессия на реальную поломку: на macOS (и в собранном приложении) у Python
нет набора корневых сертификатов, и скачивание снапшота падало с
CERTIFICATE_VERIFY_FAILED. Сети эти тесты не требуют.
"""
import ssl
import urllib.error

import pytest

from app.infrastructure import http as http_mod
from app.presentation.desktop.snapshot import _explain


@pytest.fixture(autouse=True)
def _clear_cache():
    """Контекст кэшируется — сбрасываем, иначе тесты влияют друг на друга."""
    http_mod.ssl_context.cache_clear()
    yield
    http_mod.ssl_context.cache_clear()


def test_uses_certifi_when_no_env_override(monkeypatch):
    certifi = pytest.importorskip("certifi")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    ctx = http_mod.ssl_context()
    expected = ssl.create_default_context(cafile=certifi.where())
    assert len(ctx.get_ca_certs()) == len(expected.get_ca_certs())


def test_env_override_wins_over_certifi(monkeypatch, tmp_path):
    """
    Корпоративные прокси и CI подменяют TLS своим корнем через SSL_CERT_FILE.
    Подставить туда certifi значило бы сломать рабочее окружение.
    """
    pytest.importorskip("certifi")
    bundle = tmp_path / "ca.crt"
    bundle.write_text("", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))

    ctx = http_mod.ssl_context()
    # взят системный контекст: он читает SSL_CERT_FILE, а файл пустой
    assert ctx.get_ca_certs() == []


def test_falls_back_when_certifi_missing(monkeypatch):
    """Без certifi не падаем, а берём системные сертификаты."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setitem(__import__("sys").modules, "certifi", None)

    ctx = http_mod.ssl_context()   # не должно бросить
    assert isinstance(ctx, ssl.SSLContext)


def test_context_verifies_certificates(monkeypatch):
    """Чинить SSL отключением проверки — недопустимо; проверка должна остаться."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    ctx = http_mod.ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


# ── объяснение ошибок пользователю ────────────────────────────────────────
def test_explain_certificate_error_is_human_readable():
    exc = urllib.error.URLError(ssl.SSLCertVerificationError("[SSL: CERTIFICATE_VERIFY_FAILED] ..."))
    msg = _explain(exc)
    assert "CERTIFICATE_VERIFY_FAILED" not in msg
    assert "сертификат" in msg.lower()


def test_explain_404_says_data_not_published():
    exc = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    assert "не опубликован" in _explain(exc).lower()


def test_explain_other_http_error_mentions_code():
    exc = urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)
    assert "503" in _explain(exc)


def test_explain_falls_back_to_raw_text():
    assert "что-то странное" in _explain(OSError("что-то странное"))
