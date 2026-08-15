# app/domain/universities.py
"""
Реестр вузов-источников и правила именования идентификаторов.

Пока источник был один, префикс «spbgu:» жил внутри парсера СПбГУ. Источников
стало шесть, и правило «из какого вуза эта строка» перестало быть деталью
парсера: по нему разделяются конкурсы в Монте-Карло и собираются вкладки на
сайте.

    программа   {вуз}:{направление}:{хеш названия}   spbgu:45.04.01:1f4a9c2b
    направление {вуз}:{направление}                  hse:38.04.01
    группа      {вуз}:{две цифры}                    msu:01

А вот абитуриент — НЕ неймспейснут, и это принципиально. Уникальный код
поступающего единый: его выдаёт суперсервис, и во всех вузах он один и тот же.
Значит, 1645144 в СПбГУ и 1645144 в ВШЭ — один человек, и в базе это одна
строка. Разведи мы их по вузам — сервис перестал бы понимать, что перед ним
один абитуриент, и не смог бы, например, увидеть, что согласие подано в другом
вузе.

Из этого следует, что делится по вузам не абитуриент, а КОНКУРС. Всё, что
считается внутри конкурса, хранится с указанием вуза: вероятности — по коду
программы (в нём вуз уже есть), диагностика Монте-Карло — отдельной колонкой
(см. AdmissionDiagnostics.university). Иначе прогон одного вуза затирал бы
результат другого.

Ни один идентификатор программы не должен собираться в обход этих функций:
одна ручная f-строка в стороне — и данные двух вузов смешаются молча.
"""
from __future__ import annotations

import hashlib
import re

# ── ключи вузов ────────────────────────────────────────────────────────────
SPBGU = "spbgu"
HSE = "hse"
ITMO = "itmo"
MGIMO = "mgimo"
MSU = "msu"
RANEPA = "ranepa"

#: Короткая подпись — то, что видит пользователь на вкладке.
UNIVERSITY_LABELS: dict[str, str] = {
    SPBGU: "СПбГУ",
    HSE: "ВШЭ",
    ITMO: "ИТМО",
    MGIMO: "МГИМО",
    MSU: "МГУ",
    RANEPA: "РАНХиГС",
}

#: Полное название — для страниц-справок и оговорок.
UNIVERSITY_NAMES: dict[str, str] = {
    SPBGU: "Санкт-Петербургский государственный университет",
    HSE: "Национальный исследовательский университет «Высшая школа экономики»",
    ITMO: "Университет ИТМО",
    MGIMO: "Московский государственный институт международных отношений",
    MSU: "Московский государственный университет имени М. В. Ломоносова",
    RANEPA: "Российская академия народного хозяйства и государственной службы",
}

#: Порядок вкладок на сайте и разделов в боте. Первым — вуз, с которого всё
#: начиналось и по которому данные полнее всего.
SUPPORTED_UNIVERSITIES: tuple[str, ...] = (SPBGU, HSE, ITMO, MGIMO, MSU, RANEPA)


def normalize_university(key: str | None) -> str:
    """Привести ключ вуза к каноническому виду ('  СПбГУ ' → 'spbgu')."""
    k = (key or "").strip().lower()
    return _ALIASES.get(k, k)


#: Как ключ могут написать в .env или в аргументе командной строки.
_ALIASES: dict[str, str] = {
    "спбгу": SPBGU, "spbu": SPBGU,
    "вшэ": HSE, "hse": HSE, "ниу вшэ": HSE,
    "итмо": ITMO,
    "мгимо": MGIMO,
    "мгу": MSU, "msu": MSU,
    "ранхигс": RANEPA, "ranhigs": RANEPA, "рахнигс": RANEPA,
}


def label(university: str | None) -> str:
    """Ключ вуза → подпись для человека. Незнакомый ключ показываем как есть."""
    key = normalize_university(university)
    return UNIVERSITY_LABELS.get(key, university or "")


def is_supported(university: str | None) -> bool:
    return normalize_university(university) in UNIVERSITY_LABELS


def parse_university_list(raw: str | None) -> tuple[str, ...]:
    """
    Строка из конфига → кортеж ключей вузов.

    Понимает 'all' (все поддерживаемые) и перечисление через запятую/пробел.
    Неизвестные ключи отбрасываются: опечатка в .env не должна ронять
    обновление целиком, а в логе будет видно, что вуз не подхватился.
    """
    text = (raw or "").strip()
    if not text or text.lower() in ("all", "все", "*"):
        return SUPPORTED_UNIVERSITIES
    keys = [normalize_university(part) for part in re.split(r"[,;\s]+", text) if part.strip()]
    # dict.fromkeys — уникальность с сохранением порядка, заданного человеком
    return tuple(k for k in dict.fromkeys(keys) if k in UNIVERSITY_LABELS)


# ── коды программ, направлений и групп ─────────────────────────────────────
def _normalize_name(text: str) -> str:
    """Привести название к виду, устойчивому к косметическим правкам."""
    s = (text or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"'`]", "", s)      # кавычки любого вида
    s = re.sub(r"[–—−]", "-", s)        # тире любого вида
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def stable_program_code(
    university: str,
    speciality_code: str,
    program_name: str,
    education_form: str = "",
) -> str:
    """
    Постоянный внутренний код программы.

    Идентификатор из выгрузки вуза брать нельзя: он принадлежит конкретной
    публикации списков. Когда вуз перезаливает отчёт, идентификаторы меняются,
    сохранённый каталог протухает, запросы уходят по несуществующим кодам и
    списки приходят пустыми.

    Поэтому код собирается из того, что от выгрузки не зависит: вуза, кода
    направления, названия образовательной программы и формы обучения (одна и
    та же программа бывает очной и очно-заочной). Название нормализуется —
    регистр, пробелы, кавычки и тире не должны менять код.

        stable_program_code("spbgu", "45.04.01", "Славянские языки и литературы")
        → 'spbgu:45.04.01:1f4a9c2b'
    """
    uni = normalize_university(university)
    normalized = _normalize_name(program_name)
    form = _normalize_name(education_form)
    digest = hashlib.sha1(
        f"{uni}|{speciality_code}|{normalized}|{form}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{uni}:{speciality_code}:{digest}"


def namespaced_department(university: str, speciality_code: str) -> str:
    """'01.04.02' → 'spbgu:01.04.02'.

    Коды направлений федеральные, а таблица departments не разделена по
    источникам. Префикс не даёт разным вузам делить один department_code —
    он же exam_id в Monte-Carlo, то есть баллы за разные экзамены смешались бы.
    """
    return f"{normalize_university(university)}:{speciality_code}"


def namespaced_institute(university: str, speciality_code: str) -> str:
    """'01.04.02' → 'spbgu:01' — укрупнённая группа направлений."""
    return f"{normalize_university(university)}:{speciality_code.split('.')[0]}"


def university_of_program(program_code: str) -> str | None:
    """'hse:38.04.01:ab12cd34' → 'hse'. Незнакомый префикс → None."""
    prefix = (program_code or "").split(":", 1)[0].strip().lower()
    return prefix if prefix in UNIVERSITY_LABELS else None


def display_code(code: str) -> str:
    """
    Убрать служебный префикс вуза: 'spbgu:01.04.02' → '01.04.02'.

    Пользователю префикс показывать незачем — вуз он и так видит на вкладке.
    """
    return code.split(":", 1)[-1]


# ── абитуриенты ────────────────────────────────────────────────────────────
def raw_applicant_id(key: str) -> str:
    """
    Идентификатор из базы → код, как его знает человек.

    Префикса у кода быть не должно, но старые снапшоты, собранные когда мы
    ошибочно считали коды вузовскими, лежат у людей на дисках — оттуда он
    придёт как 'spbgu:1645144'.
    """
    uni, _, rest = (key or "").partition(":")
    return rest if rest and uni.lower() in UNIVERSITY_LABELS else key


def candidate_applicant_keys(raw_code: str) -> list[str]:
    """
    Что искать в базе по коду, который ввёл человек.

    Код единый, поэтому в первую очередь ищем его как есть. Варианты с
    префиксом идут следом и нужны ровно для совместимости: в снапшотах,
    собранных до этой правки, коды лежат разложенными по вузам.
    """
    code = (raw_code or "").strip()
    if not code:
        return []
    bare = raw_applicant_id(code)
    return list(dict.fromkeys([bare, *[f"{u}:{bare}" for u in SUPPORTED_UNIVERSITIES]]))


def split_codes(raw: str) -> list[str]:
    """
    Строка из поля ввода → список кодов абитуриента.

    Код у человека один на все вузы, так что обычно он здесь один. Несколько
    всё же принимаем — за чужого посмотреть, свой и товарища сравнить, — через
    запятую или с новой строки. По пробелу НЕ режем: СНИЛС пишут как
    «123-456-789 00», и такой код развалился бы надвое.
    """
    parts = [p.strip() for p in re.split(r"[,;\n\r]+", raw or "")]
    return list(dict.fromkeys(p for p in parts if p))
