# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-спека десктоп-клиента MasterChance.

Сборка:
    pip install -r requirements-desktop.txt pyinstaller
    pyinstaller packaging/masterchance.spec

Результат зависит от системы, НА КОТОРОЙ идёт сборка (PyInstaller не умеет
собирать под чужую ОС):
    Windows → dist/MasterChance.exe
    macOS   → dist/MasterChance.app   (+ голый бинарь dist/MasterChance)
    Linux   → dist/MasterChance

На macOS обязательно нужен .app-бандл: если отдать пользователю голый
исполняемый файл, двойной клик в Finder откроет Терминал вместо окна.

Клиент НЕ считает Монте-Карло (он берёт готовый снапшот), поэтому numpy /
pandas / numba / matplotlib и парсеры на Selenium в сборку не попадают —
это и держит размер небольшим. Импорт pandas в ProgramRepository ленивый
(get_program_meta_df), так что исключение безопасно.
"""
import sys

block_cipher = None

IS_MACOS = sys.platform == "darwin"

EXCLUDES = [
    # тяжёлый счётный стек — только на сервере
    "numpy", "pandas", "numba", "llvmlite", "matplotlib", "scipy",
    # серверные/ботовые зависимости
    "aiogram", "fastapi", "uvicorn", "starlette", "jinja2",
    "selenium", "webdriver_manager", "alembic",
    # прочее, что PyInstaller любит утащить за компанию
    "IPython", "pytest", "PIL", "PySide6", "PyQt5",
]

a = Analysis(
    ["../desktop.py"],
    pathex=[".."],
    binaries=[],
    datas=[],
    hiddenimports=[
        "sqlalchemy.dialects.sqlite",
        # certifi импортируется лениво внутри функции; указываем явно, чтобы
        # сработал хук PyInstaller и набор корневых сертификатов попал в сборку.
        # Без него приложение падает с CERTIFICATE_VERIFY_FAILED.
        "certifi",
        "app.infrastructure.http",
        "app.presentation.desktop.ui",
        "app.presentation.desktop.live",
        "app.presentation.desktop.snapshot",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MasterChance",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # оконное приложение, без чёрного окна консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,       # собираем под архитектуру машины сборки
    codesign_identity=None,  # подписи нет — см. предупреждение про Gatekeeper в КАК_ЗАПУСТИТЬ.md
    entitlements_file=None,
)

# На macOS заворачиваем бинарь в .app, чтобы приложение открывалось
# двойным кликом как обычная программа, а не через Терминал.
if IS_MACOS:
    app = BUNDLE(
        exe,
        name="MasterChance.app",
        icon=None,
        bundle_identifier="ru.masterchance.desktop",
        info_plist={
            "CFBundleName": "MasterChance",
            "CFBundleDisplayName": "MasterChance",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,      # без этого окно мылит на Retina
            "LSApplicationCategoryType": "public.app-category.education",
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "MasterChance",
        },
    )
