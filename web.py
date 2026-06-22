"""Entrypoint веб-интерфейса «посмотри свои шансы» (зеркало bot.py)."""
import uvicorn

from app.config.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.presentation.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
    )
