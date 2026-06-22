# MasterChance

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.x-orange.svg)](https://docs.aiogram.dev/)
[![Docker](https://img.shields.io/badge/Docker-supported-blue.svg)](https://www.docker.com/)

Telegram-бот для абитуриентов магистратуры СПбПУ. Он парсит конкурсные списки с сайта университета, считает симуляции методом Монте-Карло и выдаёт примерную вероятность зачисления — с учётом приоритетов и возможных отказов других людей в очереди.

Честно говоря, идея не нова: многие руками смотрят на своё место в списке и прикидывают шансы. Бот просто делает это быстрее и более системно.

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

## Структура проекта

Код разбит по слоям Clean Architecture:

```
app/
  domain/         # модели данных (dataclasses)
  application/    # сценарии использования
  infrastructure/ # парсеры и работа с БД
  presentation/   # Telegram-бот (aiogram) и веб-интерфейс (FastAPI)
    web/          # FastAPI-приложение: app.py, templates/, static/
migrations/       # миграции Alembic
```

---

## Разработка

### Makefile

```bash
make build        # собрать Docker-образ
make run          # собрать и запустить
make run-web      # запустить веб-интерфейс локально (uvicorn)
make run-bot      # запустить Telegram-бот локально
make seed         # залить синтетические данные для теста
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
