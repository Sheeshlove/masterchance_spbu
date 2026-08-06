#!/usr/bin/env python3
"""
Проверка, что серверные точки входа вообще импортируются.

Ловит забытые зависимости до того, как они всплывут на сервере: тесты
работают на лёгком наборе (requirements-dev.txt), поэтому пропущенный пакет
из requirements.txt — например scipy, который тянет Monte-Carlo, — они не
замечают.

Запуск:
    pip install -r requirements.txt
    python scripts/check_imports.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Точки входа, которые запускаются на сервере.
# Не входят сюда:
#   desktop.py   — нужен tkinter (системный пакет), на сервере не запускается;
#   analytics.py — выполняет весь разбор прямо при импорте, а не под
#                  `if __name__ == "__main__"`, поэтому проверять его импортом
#                  значило бы каждый раз гонять отчёт.
ENTRYPOINTS = [
    "update_lists.py",
    "run_monte_carlo.py",
    "build_snapshot.py",
    "seed_synthetic.py",
    "bot.py",
    "web.py",
]


def import_file(path: Path) -> None:
    """Импортировать модуль по пути. SystemExit из argparse — не ошибка."""
    spec = importlib.util.spec_from_file_location(f"_check_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass


def main() -> int:
    sys.path.insert(0, str(ROOT))
    failures: list[tuple[str, str]] = []

    for name in ENTRYPOINTS:
        path = ROOT / name
        if not path.exists():
            failures.append((name, "файл не найден"))
            print(f"  ✗ {name:26} файл не найден")
            continue
        try:
            import_file(path)
        except Exception as exc:
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"  ✗ {name:26} {type(exc).__name__}: {exc}")
        else:
            print(f"  ✓ {name}")

    if failures:
        print(f"\n❌ Не импортируются: {len(failures)}")
        print("   Обычно это забытая зависимость в requirements.txt.")
        return 1

    print(f"\n✅ Все {len(ENTRYPOINTS)} точек входа импортируются.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
