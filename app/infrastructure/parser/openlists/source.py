# app/infrastructure/parser/openlists/source.py
"""
Источник открытых списков: описание вуза (SourceSpec) → IUniversitySource.

Схема одна на пять вузов:

    страница-оглавление ──(ссылки)──▶ страницы списков ──▶ таблицы/JSON ──▶ заявки

Отличается только то, что вынесено в SourceSpec: с каких адресов начинать,
какие ссылки считать списками и на сколько уровней вглубь ходить. Поэтому
новый вуз — это несколько строк в specs.py, а не новый парсер.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from app.config.logger import logger
from app.domain.universities import stable_program_code
from app.infrastructure.parser.base import (
    IUniversitySource,
    ParsedProgram,
    ProgramListing,
)
from app.infrastructure.parser.openlists.crawl import Fetched, fetch, find_links
from app.infrastructure.parser.openlists.records import (
    ProgramFacts,
    is_paid,
    json_rows_to_applications,
    looks_like_json_ranking,
    make_stats,
    parse_generated_at,
    program_facts,
    table_to_applications,
)
from app.infrastructure.parser.openlists.sheets import tables_from

_MSK = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class SourceSpec:
    """
    Где у вуза лежат списки магистратуры.

    index_urls    — с чего начинать обход (страница «Списки поступающих»).
    list_pattern  — какие ссылки на них ведут к самим спискам (по url или по
                    тексту ссылки). None — значит index_urls уже списки.
    index_pattern — какие ссылки вести обход дальше вглубь (факультеты,
                    институты). Работает, пока не исчерпан follow_depth.
    link_required — каждая регулярка обязана найтись в разделе, подписи или
                    адресе ссылки. Так отбираются нужные кампусы и очная форма.
    link_excluded — ни одна не должна найтись: заочное, платное, чужие города.
    json_ref_template — если оглавление приходит JSON-ответом API: шаблон
                    адреса списка, куда подставится найденный идентификатор.
    """
    university: str
    index_urls: tuple[str, ...]
    list_pattern: str | None = None
    index_pattern: str | None = None
    link_required: tuple[str, ...] = ()
    link_excluded: tuple[str, ...] = ()
    follow_depth: int = 1
    json_ref_template: str | None = None
    max_lists: int = 400
    #: Человеческое пояснение — печатается диагностикой.
    note: str = ""


@dataclass
class _PageProgram:
    """Промежуточный результат: один конкурс, найденный на странице."""
    facts: ProgramFacts
    applications: list = field(default_factory=list)


class OpenListsSource(IUniversitySource):
    """Универсальный источник списков по описанию SourceSpec."""

    def __init__(self, spec: SourceSpec, timeout: int = 60) -> None:
        self.spec = spec
        self.university = spec.university
        self._timeout = timeout

    # ── discovery ──────────────────────────────────────────────────────────
    def discover(self) -> list[ProgramListing]:
        spec = self.spec
        if spec.list_pattern is None and not spec.json_ref_template:
            return [ProgramListing(ref=url) for url in spec.index_urls]

        found: dict[str, str] = {}          # url → подпись
        frontier: list[str] = list(spec.index_urls)
        visited: set[str] = set()

        for depth in range(max(1, spec.follow_depth)):
            next_frontier: list[str] = []
            for url in frontier:
                if url in visited or len(found) >= spec.max_lists:
                    continue
                visited.add(url)
                try:
                    page = fetch(url, timeout=self._timeout)
                except Exception as exc:  # noqa: BLE001 — один битый раздел не ломает обход
                    logger.warning("[%s] Оглавление недоступно (%s): %s", self.university, url, exc)
                    continue

                if page.is_json and spec.json_ref_template:
                    for ref, title in _json_refs(page, spec.json_ref_template):
                        found.setdefault(ref, title)
                    continue

                for link in find_links(page.body, url, spec.list_pattern,
                                       spec.link_required, spec.link_excluded):
                    found.setdefault(link.url, link.text)
                if depth + 1 < spec.follow_depth and spec.index_pattern:
                    # Вглубь ходим по тем же запретам (не заходим в чужие
                    # кампусы и в заочное), но без требований: раздел
                    # факультета может не повторять их у себя в заголовке.
                    #
                    # Ссылка, уже признанная списком, вглубь не разворачивается:
                    # «…/magistratura/spiski/…» подходит под оба шаблона, и без
                    # этого условия каждый список скачивался бы ещё и как раздел.
                    next_frontier.extend(
                        link.url for link in find_links(
                            page.body, url, spec.index_pattern, excluded=spec.link_excluded)
                        if link.url not in visited and link.url not in found
                    )
            frontier = next_frontier
            if not frontier:
                break

        listings = [ProgramListing(ref=url, title=title) for url, title in found.items()]
        if len(listings) > spec.max_lists:
            logger.warning(
                "[%s] Найдено %d списков, берём первые %d (см. MAX_LISTS_PER_SOURCE)",
                self.university, len(listings), spec.max_lists,
            )
            listings = listings[: spec.max_lists]
        return listings

    # ── разбор одного списка ───────────────────────────────────────────────
    def fetch(self, listing: ProgramListing) -> list[ParsedProgram]:
        try:
            page = fetch(listing.ref, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 — страница может быть снята; это не сбой прохода
            logger.warning("[%s] Список недоступен (%s): %s", self.university, listing.ref, exc)
            return []

        # Дату публикации ищем только в тексте: у файла Excel её взять неоткуда,
        # там она если и есть, то в шапке листа — а её мы читаем ниже.
        generated_at = (
            None if page.is_binary else parse_generated_at(page.body)
        ) or datetime.now(_MSK).replace(tzinfo=None)
        programs = (
            self._from_json(page, listing) if page.is_json else self._from_tables(page, listing)
        )
        return self._to_parsed(programs, generated_at)

    def _from_tables(self, page: Fetched, listing: ProgramListing) -> list[_PageProgram]:
        """Страница, файл Excel или CSV — дальше всё одинаково."""
        out: list[_PageProgram] = []
        for table in tables_from(page):
            # Отдельный лист/таблица под платные места — не наш конкурс.
            # У ВШЭ, например, бюджет и договор лежат соседними листами одной
            # книги, и без этой проверки платники попали бы в бюджетный список.
            if is_paid(f"{table.preamble} {table.page_title} {listing.title}"):
                logger.debug("[%s] Пропускаем платный конкурс: %s",
                             self.university, table.page_title or listing.title)
                continue
            facts = program_facts(
                # Заголовок над таблицей — самый точный источник; если его нет,
                # берём заголовок страницы, а в крайнем случае текст ссылки.
                table.preamble or table.page_title or listing.title,
                fallback_name=listing.title or table.page_title,
            )
            applications = table_to_applications(table, program_code="")
            if applications:
                out.append(_PageProgram(facts=facts, applications=applications))
        return out

    def _from_json(self, page: Fetched, listing: ProgramListing) -> list[_PageProgram]:
        try:
            payload = page.json()
        except ValueError:
            return []

        out: list[_PageProgram] = []
        for rows, context in _iter_row_arrays(payload):
            if not looks_like_json_ranking(rows):
                continue
            facts = program_facts(context or listing.title, fallback_name=listing.title)
            applications = json_rows_to_applications(rows, program_code="")
            if applications:
                out.append(_PageProgram(facts=facts, applications=applications))
        return out

    def _to_parsed(self, programs: Iterable[_PageProgram], generated_at: datetime) -> list[ParsedProgram]:
        """
        Присвоить конкурсам наши коды и слить одинаковые.

        Две таблицы на странице могут дать один и тот же код — например, когда
        список разбит по страницам или продублирован. В базе ключ один, поэтому
        сливаем здесь, а не оставляем на upsert: иначе вторая таблица молча
        затрёт первую.
        """
        merged: dict[str, ParsedProgram] = {}
        for item in programs:
            facts = item.facts
            if not facts.program_name:
                continue
            code = stable_program_code(
                self.university, facts.speciality_code, facts.program_name, facts.education_form,
            )
            existing = merged.get(code)
            applications = [
                # program_code проставляется здесь: до этого момента его не из
                # чего было вычислить — он выводится из названия программы.
                _with_code(app, code) for app in item.applications
            ]
            if existing is None:
                merged[code] = ParsedProgram(
                    program_code=code,
                    program_name=facts.program_name,
                    speciality_code=facts.speciality_code,
                    education_form=facts.education_form,
                    is_international="ждунар" in facts.program_name.lower(),
                    stats=make_stats(code, facts.num_places, applications, generated_at),
                    applications=applications,
                )
            else:
                known = {a.applicant_id for a in existing.applications}
                existing.applications.extend(a for a in applications if a.applicant_id not in known)
                existing.stats.num_places = max(existing.stats.num_places, facts.num_places)
                existing.stats.num_applications = len(existing.applications)
        return list(merged.values())


def _with_code(app, code: str):
    app.program_code = code
    return app


def _iter_row_arrays(payload: Any, context: str = "", depth: int = 0):
    """
    Обойти JSON и выдать все массивы объектов вместе с их «контекстом».

    Контекст — накопленные по пути названия (program_name, name, title): из них
    потом выводится название программы. Так разбор не зависит от того, как
    именно API вуза называет свои обёртки.
    """
    if depth > 6:
        return
    if isinstance(payload, list):
        dicts = [item for item in payload if isinstance(item, dict)]
        if dicts:
            yield dicts, context
        for item in payload:
            if isinstance(item, (dict, list)):
                yield from _iter_row_arrays(item, context, depth + 1)
    elif isinstance(payload, dict):
        local = context
        for key in ("program_name", "programName", "competition", "name", "title", "speciality"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                local = f"{local} {value}".strip()
                break
        for value in payload.values():
            if isinstance(value, (dict, list)):
                yield from _iter_row_arrays(value, local, depth + 1)


def _json_refs(page: Fetched, template: str) -> list[tuple[str, str]]:
    """
    JSON-оглавление → адреса списков.

    Ищем объекты, у которых есть идентификатор и название: у API вузов
    справочник программ выглядит именно так, как бы ни назывались обёртки.
    """
    try:
        payload = page.json()
    except ValueError:
        return []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for rows, _ in _iter_row_arrays(payload):
        for row in rows:
            ident = next(
                (str(row[k]) for k in ("id", "program_id", "programId", "isu_id", "uuid", "code")
                 if row.get(k) not in (None, "")),
                None,
            )
            if not ident:
                continue
            title = next(
                (str(row[k]) for k in ("name", "title", "program_name", "programName")
                 if isinstance(row.get(k), str)),
                "",
            )
            ref = template.replace("{id}", ident)
            if ref not in seen:
                seen.add(ref)
                out.append((ref, title))
    return out


def build_source(spec: SourceSpec, timeout: int = 60) -> OpenListsSource:
    return OpenListsSource(spec, timeout=timeout)


#: Заголовки, по которым видно, что страница вообще не про списки, — чтобы
#: диагностика могла сказать об этом человеческим языком.
NOT_A_LIST_HINTS = re.compile(r"(404|не найдена|страница не существует|access denied)", re.I)
