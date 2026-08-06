FROM python:3.11-slim

# Браузер не нужен: единственный источник данных — отчёт СПбГУ, который
# читается обычными HTTP-запросами. Раньше здесь ставился Chromium со всей
# обвязкой ради Selenium (~1,5 ГБ образа).

# curl нужен scripts/publish_snapshot.sh — в slim-образе его нет.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

ENV ENV=dev \
    DATA_DIR=./data \
    TIMEZONE=Europe/Moscow \
    DB_FILENAME=master.db \
    DB_ECHO=false \
    UNIVERSITY=spbgu \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8080

EXPOSE 8080

# ENTRYPOINT — интерпретатор; что запускать, задаёт CMD (по умолчанию бот).
# Веб:      docker run <img> web.py
# Списки:   docker run <img> update_lists.py
ENTRYPOINT ["python"]
CMD ["bot.py"]
