"""
Корневой conftest.

Две задачи:
  1) положить корень репозитория в sys.path, чтобы `import app...` работал
     из любого тестового файла (это делает сам pytest, раз файл лежит здесь);
  2) увести тесты с рабочей БД: модули вроде app/presentation/bot.py на
     импорте создают движок по settings.database_url, а settings читается
     один раз при первом импорте конфига. Поэтому DATABASE_URL подменяется
     ДО того, как что-либо из app будет импортировано.
"""
import os
import tempfile
from pathlib import Path

_TEST_DB = Path(tempfile.gettempdir()) / "masterchance-tests" / "test.db"
_TEST_DB.parent.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB}")
