# MasterChance

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.x-orange.svg)](https://docs.aiogram.dev/)
[![Docker](https://img.shields.io/badge/Docker-supported-blue.svg)](https://www.docker.com/)

Telegram-бот для абитуриентов магистратуры СПбПУ. Он парсит конкурсные списки с сайта университета, считает симуляции методом Монте-Карло и выдаёт примерную вероятность зачисления — с учётом приоритетов и возможных отказов других людей в очереди.

Честно говоря, идея не нова: многие руками смотрят на своё место в списке и прикидывают шансы. Бот просто делает это быстрее и более системно.

> 🚀 **Никогда этим не пользовались?** Пошаговая инструкция без единого
> технического слова — [КАК_ЗАПУСТИТЬ.md](КАК_ЗАПУСТИТЬ.md).

---

## Что умеет бот

- Собирает данные о заявлениях через Selenium — без ручного обновления.
- Запускает симуляции Монте-Карло поверх актуальных данных и показывает вероятность поступления.
- Следит за расписанием вступительных испытаний.
- Строит отчёты по проходным баллам и числу поданных заявлений на направление.

---

## Технологии

| Цель          | Инструмент                      |
| ------------- | ------------------------------- |
| Бот           | `aiogram 3.x`                   |
| БД и миграции | `SQLAlchemy`, `Alembic`, SQLite |
| Парсинг       | `Selenium`, `webdriver-manager` |
| Расчёты       | `numpy`, `numba`, `pandas`      |
| Графики       | `matplotlib`                    |
| Сборка        | Docker                          |

---

## Запуск

### Требования

- Python 3.11+
- Токен Telegram-бота от [@BotFather](https://t.me/BotFather)
- Docker (если запускаете в контейнере) или Chromium (если локально)

### Через Docker

```bash
git clone <repository_url>
cd masterchance
```

Создайте `.env`:

```env
BOT_TOKEN=your_token_here
ENV=dev
```

Запустите:

```bash
make run
```

### Локально

```bash
pip install -r requirements.txt
python bot.py
```

Парсер использует Selenium — нужен установленный Chromium или Chrome.

### Веб-интерфейс «посмотри свои шансы»

Кроме бота есть сайт с тем же сценарием: вводишь **код абитуриента** — видишь
направления, шанс зачисления, проходные баллы и статус экзаменов. Это read-only
витрина поверх той же БД; вся логика расчётов — в общем
`GetApplicantForecastUseCase`, который используют и бот, и сайт (числа не расходятся).

```bash
pip install -r requirements.txt
python seed_synthetic.py        # синтетические данные для локального теста (опц.)
python web.py                   # → http://localhost:8080  (make run-web)
```

Хост/порт настраиваются через `WEB_HOST` / `WEB_PORT` (по умолчанию `0.0.0.0:8080`).
Маршруты: `/` — форма и результат, `/how` — как работает прогноз, `/healthz` — проверка живости.

#### В Docker

Образ один на бот и веб; что запускать — задаёт команда (`CMD` по умолчанию — бот):

```bash
make web-docker          # только сайт в контейнере → http://localhost:8080
make compose-up          # бот + веб вместе (docker compose, общая БД через том ./data)
make compose-down        # остановить
```

`docker-compose.yml` поднимает два сервиса (`bot`, `web`) на одном образе и общей
БД (`./data`); переменные берутся из `.env`.

---

## Десктоп-клиент (.exe)

Пользователь открывает `MasterChance.exe`, вводит свой уникальный код поступающего
и видит шансы — без браузера и без установки Python.

### Как это устроено (и почему именно так)

Монте-Карло **нельзя посчитать для одного человека**: модель разыгрывает конкурс
всей когорты (см. `RecalculateMonteCarloUseCase` — он читает `get_all_applications()` /
`get_all_applicants()`). Поэтому клиент не парсит все списки на машине пользователя
— это заняло бы минуты, раздуло `.exe` до сотен мегабайт и обрушило бы сотни
запросов на сервер вуза с каждого компьютера.

Вместо этого:

| Что | Откуда | Свежесть |
| --- | --- | --- |
| Шансы, проходные баллы | **снапшот** БД с посчитанным MC (скачивается и кэшируется) | по расписанию пересчёта на сервере |
| Ваши баллы, приоритеты, согласия | **живой парсинг** по фильтру `applicant_code` — один запрос | в момент нажатия кнопки |

Расчёты не дублируются: клиент, бот и сайт используют один и тот же
`GetApplicantForecastUseCase`, поэтому числа нигде не расходятся.

Если сети нет — клиент честно работает на сохранённом снапшоте и пишет об этом
в статусной строке.

### Запуск из исходников

```bash
pip install -r requirements-desktop.txt
python desktop.py          # или make run-desktop
```

Откуда качать снапшот, задаёт `SNAPSHOT_URL` (по умолчанию — GitHub Releases).
Кэш лежит в `%LOCALAPPDATA%\MasterChance` (Windows) или `~/.local/share/masterchance`.

### Публикация снапшота (серверная сторона)

После `update_lists.py` и `run_monte_carlo.py`:

```bash
make snapshot              # → dist/master-snapshot.db.gz
```

Полученный файл выкладывается туда, куда смотрит `SNAPSHOT_URL` (например,
в GitHub Releases с именем `master-snapshot.db.gz`). Клиент забирает его
условным GET, так что неизменившийся снапшот повторно не качается.

### Сборка приложения

PyInstaller собирает только под ту систему, на которой запущен, поэтому сборка
матричная — workflow `.github/workflows/build-desktop.yml` (вручную или по тегу
`v*`) прогоняет офлайн-тесты и собирает три варианта:

| Раннер | Артефакт | Для кого |
| --- | --- | --- |
| `windows-latest` | `MasterChance.exe` | Windows |
| `macos-14` | `MasterChance-macos-apple-silicon.zip` | Mac на M1/M2/M3/M4 |
| `macos-13` | `MasterChance-macos-intel.zip` | Mac на Intel |

На macOS собирается полноценный `.app`-бандл (иначе двойной клик в Finder
открыл бы Терминал), и пакуется через `ditto` — `upload-artifact` не сохраняет
флаг «исполняемый».

Локально:

```bash
pip install -r requirements-desktop.txt pyinstaller
pyinstaller packaging/masterchance.spec
# Windows → dist/MasterChance.exe   macOS → dist/MasterChance.app
```

Приложение **не подписано**: Windows покажет SmartScreen, macOS — «не удаётся
проверить разработчика». Как это обойти, расписано в
[КАК_ЗАПУСТИТЬ.md](КАК_ЗАПУСТИТЬ.md).

В сборку намеренно не входят `numpy/pandas/numba/selenium` — считает сервер,
клиенту нужен только SQLite и HTTP.

---

## Обновление данных

Скрипты запускаются вручную или по расписанию:

- `update_lists.py` — обновляет списки абитуриентов.
- `update_exam_schedule.py` — обновляет расписание испытаний.
- `run_monte_carlo.py` — пересчитывает вероятности.

---

## Полный алгоритм запуска и использования

Поток данных: **программы в БД → списки заявлений → Монте-Карло → бот отдаёт вероятности**.

### 1. Окружение

Требуется Python 3.11+, Chromium/Chrome (для Selenium) и токен бота от [@BotFather](https://t.me/BotFather).

```bash
git clone <repo_url> && cd masterchance_spbu
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Конфигурация `.env`

```env
BOT_TOKEN=<токен_от_BotFather>
ENV=dev
TIMEZONE=Europe/Moscow
DATABASE_URL=sqlite:///data/master.db
PARSER_PARALLELISM=8                 # число параллельных ChromeDriver

# Выбор вуза-источника
UNIVERSITY=spbpu                     # spbpu (Политех) | spbgu (СПбГУ)
SPBGU_BASE_URL=https://cabinet.spbu.ru/Lists/AG_Rating/
```

Полный список параметров (отток, freeze экзаменов и т.д.) — в `app/config/config.py`.

### 3. Инициализация БД

- **Свежая БД** — таблицы создаются автоматически при первом запуске любого скрипта
  (`Base.metadata.create_all`), уже с колонкой `university`.
- **Существующая БД** (была до мультивуза) — накатить миграцию; она добавит `university`
  и проставит всем старым строкам `spbpu`:

  ```bash
  alembic upgrade head        # включает миграцию a1c2e3f4b5d6_add_university
  ```

### 4. Заполнение программ (сидинг) — до `update_lists`

`update_lists.py` берёт список программ **из БД** (`get_programs_by_university`), поэтому
программы/кафедры/институты должны быть засеяны заранее.

- **СПбПУ** — программы заводятся через `repo.add_institute/add_department/add_program`
  (источник кодов — `scripts/parse_programs.py`, селектор `#code` на my.spbstu.ru).
- **СПбГУ** — сидинг на базе `spbgu_programs.discover_programs()` запишет программы с
  `university='spbgu'` (каркас; ждёт разведки формата cabinet.spbu.ru).

### 5. Обновление данных (по вузам)

```bash
# СПбПУ (UNIVERSITY из .env по умолчанию):
python update_lists.py
python update_lists.py --university=spbpu     # то же явно

# СПбГУ (после реализации парсера):
python update_lists.py --university=spbgu

# только пересчёт MC / расписание ВИ:
python run_monte_carlo.py
python update_exam_schedule.py
```

`update_lists.py` парсит списки в N процессов (один ChromeDriver на процесс), перезаписывает
заявки выбранного вуза, обновляет статистику и **сразу пересчитывает Монте-Карло**.

### 6. Запуск бота

```bash
python bot.py     # или make run (Docker)
```

Бот по коду абитуриента ищет его заявки и показывает вероятности из последнего пересчёта MC.

### 7. Регулярная эксплуатация (cron)

```cron
0  */3 * * *  cd /app && python update_lists.py --university=spbpu
30 */3 * * *  cd /app && python update_lists.py --university=spbgu   # после реализации парсера
0  4   * * *  cd /app && python update_exam_schedule.py
```

Бот держится отдельным долгоживущим процессом; скрипты обновления дописывают БД, бот читает
свежие результаты.

### Статус мультивуза

- ✅ **Готово:** колонка `university` + миграция, фильтрация по вузу в репозитории, выбор
  источника (`UNIVERSITY` / `--university=`), фабрика парсеров, абстракция
  `IApplicationsParser`. Путь СПбПУ не изменился.
- ⛔ **Не готово:** парсер СПбГУ (`SpbguMasterApplicationsParser.parse`) и `discover_programs` —
  каркасы, бросают `NotImplementedError`. Доделка требует Selenium-разведки `cabinet.spbu.ru`
  (Фаза 0).

---

## Тесты

```bash
pip install -r requirements-dev.txt
make test            # или python -m pytest
```

Весь набор офлайновый: сеть не нужна (запросы к серверу вуза подменяются, а
скачивание снапшота проверяется на локальном HTTP-сервере), рабочая
`data/master.db` не трогается — корневой `conftest.py` уводит тесты на временную
БД. Тесты рендеринга бота пропускаются, если не установлен `aiogram`.

Что покрыто:

| Файл | О чём |
| --- | --- |
| `test_forecast_use_case.py` | ядро прогноза: условные вероятности, «пролёт», порядок направлений, неполные данные |
| `test_exam_status.py` | статус ВИ: баллы / ближайшие даты / расписания нет / экзамены прошли |
| `test_bot_render.py` | Markdown бота и нарезка под лимит Telegram |
| `test_web_view.py` | контекст Jinja-шаблонов, форматирование процентов и проходных |
| `test_spbgu_list.py`, `test_spbgu_parsing_edges.py` | разбор списков СПбГУ, два ВИ, битые ячейки, даты |
| `test_spbgu_discovery.py` | справочник программ из reportMeta |
| `test_desktop_snapshot.py` | скачивание снапшота, 304, офлайн-фолбэк |
| `test_desktop_live.py` | свежие личные данные и защита от шторма запросов |
| `test_build_snapshot.py` | сборка снапшота и отказ собирать бесполезный |

---

## Структура проекта

Код разбит по слоям Clean Architecture:

```
app/
  domain/         # модели данных (dataclasses)
  application/    # сценарии использования
  infrastructure/ # парсеры и работа с БД
  presentation/   # Telegram-бот (aiogram), веб-интерфейс (FastAPI), десктоп (tkinter)
    web/          # FastAPI-приложение: app.py, templates/, static/
    desktop/      # десктоп-клиент: ui.py, snapshot.py, live.py
migrations/       # миграции Alembic
packaging/        # спека PyInstaller для сборки .exe
tests/            # офлайн-тесты на фикстурах
```

---

## Разработка

### Makefile

```bash
make build        # собрать Docker-образ
make run          # собрать и запустить (бот)
make run-web      # запустить веб-интерфейс локально (uvicorn)
make run-bot      # запустить Telegram-бот локально
make run-desktop  # запустить десктоп-клиент из исходников
make seed         # залить синтетические данные для теста
make snapshot     # собрать снапшот БД для десктоп-клиента
make exe          # собрать MasterChance.exe (только Windows)
make test         # прогнать офлайн-тесты
make web-docker   # запустить веб в контейнере (порт 8080)
make compose-up   # бот + веб через docker compose
make compose-down # остановить docker compose
make push         # отправить образ в реестр
make bump-version # обновить версию (VERSION=x.y.z)
```

### Миграции

```bash
alembic upgrade head
```

---

## Лицензия

Данные со страниц университета принадлежат СПбПУ. Бот использует их только для расчётов и не хранит в открытом доступе.
