"""Entrypoint веб-интерфейса «посмотри свои шансы» (зеркало bot.py)."""
import uvicorn

from app.config.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.presentation.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        # За nginx приложение видит запрос как обычный http и, если поверит
        # своим глазам, соберёт абсолютные ссылки с http:// — на https-странице
        # браузер и Telegram заблокируют их как mixed content.
        #
        # По умолчанию uvicorn доверяет заголовкам X-Forwarded-* только от
        # 127.0.0.1, а из контейнера nginx приходит с адреса docker-шлюза, и
        # заголовки молча игнорируются. Отсюда "*".
        #
        # Это безопасно ровно потому, что порт опубликован как
        # 127.0.0.1:8080 (docker-compose.yml): снаружи достучаться до него
        # нельзя, подделать заголовок может только сам nginx.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
