# app/infrastructure/http.py
"""
Единая точка исходящих HTTPS-запросов.

Зачем: `urllib` берёт корневые сертификаты у OpenSSL, а на macOS их там
попросту нет — Python не ходит в системную связку ключей. В собранном
PyInstaller-приложении набора сертификатов нет тем более. В обоих случаях
запрос падает с `SSL: CERTIFICATE_VERIFY_FAILED`, даже если файл на месте
и интернет работает.

Порядок выбора сертификатов важен:

1. Если заданы `SSL_CERT_FILE` / `SSL_CERT_DIR`, используем системный контекст —
   OpenSSL подхватит их сам. Это случай корпоративных прокси и CI, которые
   подменяют TLS своим корневым сертификатом: подставить туда certifi значило бы
   сломать заведомо рабочее окружение.
2. Иначе берём набор из `certifi` — он и чинит macOS и собранные приложения.
3. Если certifi нет — системный контекст, чтобы ничего не падало на ровном месте.
"""
from __future__ import annotations

import os
import ssl
import urllib.request
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """SSL-контекст с корректными корневыми сертификатами для текущей системы."""
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context()

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()

    try:
        return ssl.create_default_context(cafile=certifi.where())
    except OSError:
        # файл сертификатов недоступен — лучше системный, чем никакой
        return ssl.create_default_context()


def urlopen(req: Any, timeout: int = 60):
    """
    `urllib.request.urlopen` с нашим SSL-контекстом.

    Все исходящие запросы проекта идут через неё: так ни один вызов не может
    случайно остаться без корректных сертификатов.
    """
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())  # noqa: S310
