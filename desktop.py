"""Entrypoint десктоп-клиента (зеркало bot.py / web.py).

Запуск из исходников:  python desktop.py
Сборка .exe:           см. packaging/masterchance.spec и README.
"""
from app.config.config import settings
from app.presentation.desktop.ui import DesktopApp


def main() -> None:
    DesktopApp(snapshot_url=settings.snapshot_url).run()


if __name__ == "__main__":
    main()
