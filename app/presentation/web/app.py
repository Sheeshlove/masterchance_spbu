"""
Веб-интерфейс «посмотри свои шансы».

Read-only витрина поверх той же БД, что и Telegram-бот: пользователь вводит
код абитуриента и видит направления, шансы зачисления, проходные баллы и
статус экзаменов. Вся бизнес-логика — в общем
`GetApplicantForecastUseCase`; здесь только HTTP + рендеринг HTML.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.use_cases.get_applicant_forecast import GetApplicantForecastUseCase
from app.application.use_cases.get_last_update_time import GetLastUpdateTimeUseCase
from app.config.config import settings
from app.infrastructure.db.models import Base
from app.infrastructure.db.repositories.program_repository import ProgramRepository
from app.presentation import content
from app.presentation.web.view import fmt_update, to_view

_BASE_DIR = Path(__file__).resolve().parent

_engine = create_engine(settings.database_url, echo=False, future=True)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, future=True)

app = FastAPI(title="MasterChance — посмотри свои шансы")
app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

# Подвал показывается на каждой странице, поэтому его текст — глобальный:
# иначе его пришлось бы прокидывать в контекст каждого маршрута и рано или
# поздно забыть в одном из них.
templates.env.globals.update(
    footer_note_lead=content.FOOTER_NOTE_LEAD,
    contact_email=content.CONTACT_EMAIL,
    credit_lead=content.CREDIT_LEAD,
    original_author=content.ORIGINAL_AUTHOR,
    original_author_url=content.ORIGINAL_AUTHOR_URL,
    repo_url=content.REPO_URL,
    repo_label=content.REPO_LABEL,
    disclaimer_short=content.DISCLAIMER_SHORT,
)


def get_repo() -> ProgramRepository:
    """Сессия БД на время запроса."""
    session: Session = _Session()
    try:
        yield ProgramRepository(session)
    finally:
        session.close()


def _lookup(repo: ProgramRepository, code: str):
    """Возвращает (view | None, not_found_code | None)."""
    code = (code or "").strip()
    if not code:
        return None, None
    result = GetApplicantForecastUseCase(repo).execute(code)
    if result is None:
        return None, code
    return to_view(result), None


@app.get("/", response_class=HTMLResponse)
def index(request: Request, code: str = "", repo: ProgramRepository = Depends(get_repo)):
    view, not_found = _lookup(repo, code)
    last_update = fmt_update(GetLastUpdateTimeUseCase(repo).execute())
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "code": code.strip(),
            "view": view,
            "not_found": not_found,
            "last_update": last_update,
        },
    )


@app.get("/result", response_class=HTMLResponse)
def result(request: Request, code: str = "", repo: ProgramRepository = Depends(get_repo)):
    """Партиал результата (для HTMX-подмены)."""
    view, not_found = _lookup(repo, code)
    return templates.TemplateResponse(
        request,
        "result.html",
        {"code": code.strip(), "view": view, "not_found": not_found},
    )


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return templates.TemplateResponse(request, "how.html")


@app.get("/mechanism", response_class=HTMLResponse)
def mechanism(request: Request):
    """Весь путь данных: откуда берутся, что с ними происходит, что не собирается."""
    return templates.TemplateResponse(
        request,
        "mechanism.html",
        {
            "mechanism": content.MECHANISM,
            "mechanism_title": content.MECHANISM_TITLE,
            "mechanism_intro": content.MECHANISM_INTRO,
        },
    )


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"
