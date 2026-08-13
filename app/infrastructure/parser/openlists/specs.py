# app/infrastructure/parser/openlists/specs.py
"""
По одному описанию источника на вуз.

Что здесь описано и что нет
───────────────────────────
Описано: с какой страницы начинать обход, какие ссылки вести дальше и какие
считать самими списками. НЕ описана вёрстка: колонки узнаются по заголовкам
(columns.py), поэтому редизайн сайта вуза сам по себе ничего не ломает.

Про адреса. Раздел приёмной кампании вузы переносят почти каждый год — вместе
со сменой домена, года в пути и названия раздела. Поэтому:

  • адрес по умолчанию — стартовая страница приёма, а не «глубокая» ссылка на
    списки: она живёт дольше, а до списков обход дойдёт сам;
  • каждый адрес переопределяется переменной окружения (HSE_LISTS_URL,
    ITMO_LISTS_URL, MGIMO_LISTS_URL, MSU_LISTS_URL, RANEPA_LISTS_URL) —
    менять код ради нового адреса не нужно;
  • перед сезоном адрес стоит сверять: `python scripts/diagnose_source.py hse`
    покажет, сколько списков нашлось и какие колонки в них узнались.

Сбой одного вуза не трогает остальные: обновление идёт по источникам
независимо (см. UpdateApplicationListsUseCase.execute_source).
"""
from __future__ import annotations

from app.domain.universities import HSE, ITMO, MGIMO, MSU, RANEPA
from app.infrastructure.parser.openlists.source import SourceSpec

#: Ссылка на сам рейтинговый список — по адресу или по подписи ссылки.
#: Подпись важнее: у половины вузов адрес — это /upload/iblock/… с хешем,
#: и узнать список можно только по тексту «Рейтинговый список».
_LIST_LINK = (
    r"(?:spisk|spisok|spiski|rating|reyting|rejting|konkurs|ranking|"
    r"список|списки|рейтинг|конкурсн|подавших|поступающих)"
)

#: Ссылка вглубь: факультет, институт, кампус, направление. По ним обход идёт
#: только пока не исчерпан follow_depth.
_INDEX_LINK = (
    r"(?:magistr|master|priem|abitur|postuplen|fakultet|institut|campus|"
    r"магистр|приём|прием|абитуриент|факультет|институт|направлен)"
)

#: Списков у большого вуза сотни; ограничение бережёт и нас, и сервер вуза.
_MAX_LISTS = 400


def _spec(university: str, url: str, *, depth: int = 2, note: str = "",
          max_lists: int = _MAX_LISTS) -> SourceSpec:
    return SourceSpec(
        university=university,
        index_urls=(url,),
        list_pattern=_LIST_LINK,
        index_pattern=_INDEX_LINK,
        follow_depth=depth,
        max_lists=max_lists,
        note=note,
    )


#: Стартовые адреса по умолчанию. Переопределяются через .env — см. config.py.
DEFAULT_INDEX_URLS: dict[str, str] = {
    HSE: "https://enrol.hse.ru/",
    ITMO: "https://abit.itmo.ru/rating/master",
    MGIMO: "https://mgimo.ru/priem/",
    MSU: "https://cpk.msu.ru/",
    RANEPA: "https://www.ranepa.ru/abiturient/",
}

_NOTES: dict[str, str] = {
    HSE: (
        "Списки ВШЭ живут в отдельном сервисе приёмной кампании (enrol.hse.ru); "
        "у вуза несколько кампусов, поэтому обход идёт на два уровня."
    ),
    ITMO: (
        "Страница рейтингов ИТМО отрисовывается скриптом на стороне браузера. "
        "Если обход не нашёл ни одного списка, укажите в ITMO_LISTS_URL адрес "
        "JSON-ответа, который страница запрашивает у своего API (виден в "
        "инструментах разработчика на вкладке Network) — движок разберёт и его."
    ),
    MGIMO: "МГИМО публикует списки в разделе приёма, отдельной страницей на конкурс.",
    MSU: "У МГУ приём ведут факультеты, поэтому списки лежат на два уровня вглубь.",
    RANEPA: "РАНХиГС публикует списки по факультетам и филиалам.",
}


def default_spec(university: str, index_url: str | None = None) -> SourceSpec:
    """
    Описание источника для вуза. `index_url` перекрывает адрес по умолчанию.

    Отдельного класса на вуз нет намеренно: пока различия сводятся к адресу и
    глубине обхода, пять почти одинаковых классов только маскировали бы это.
    """
    url = (index_url or DEFAULT_INDEX_URLS.get(university) or "").strip()
    # МГУ и РАНХиГС — федерации факультетов и филиалов, до списка три клика.
    depth = 3 if university in (MSU, RANEPA) else 2
    return _spec(university, url, depth=depth, note=_NOTES.get(university, ""))


SUPPORTED = tuple(DEFAULT_INDEX_URLS)
