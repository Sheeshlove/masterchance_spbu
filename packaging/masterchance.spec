# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-спека десктоп-клиента MasterChance.

Сборка (на Windows):
    pip install -r requirements-desktop.txt pyinstaller
    pyinstaller packaging/masterchance.spec

Результат: dist/MasterChance.exe (onefile, без консольного окна).

Клиент НЕ считает Монте-Карло (он берёт готовый снапшот), поэтому numpy /
pandas / numba / matplotlib и парсеры на Selenium в сборку не попадают —
это и держит .exe небольшим. Импорт pandas в ProgramRepository ленивый
(get_program_meta_df), так что исключение безопасно.
"""

block_cipher = None

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
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
