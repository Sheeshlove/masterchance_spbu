# app/domain/universities.py
"""
Реестр вузов-источников и правила именования идентификаторов.

Пока источник был один, префикс «spbgu:» жил внутри парсера СПбГУ. Источников
стало шесть, и правило «из какого вуза эта строка» перестало быть деталью
парсера: по нему разделяются конкурсы в Монте-Карло, по нему собираются вкладки
на сайте, по нему считается, что абитуриент 1645144 в СПбГУ и абитуриент
1645144 в МГУ — два разных человека.

Поэтому правила лежат здесь, в домене, и одинаковы для всех:

    программа   {вуз}:{направление}:{хеш названия}   spbgu:45.04.01:1f4a9c2b
    направление {вуз}:{направление}                  hse:38.04.01
    группа      {вуз}:{две цифры}                    msu:01
    абитуриент  {вуз}:{код из списков}               itmo:1645144

Ни один идентификатор не должен собираться в обход этих функций: одна ручная
f-строка в стороне — и данные двух вузов смешаются молча.
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
def applicant_key(university: str, raw_id: str) -> str:
    """
    Код из списков → идентификатор в нашей базе: 'hse:1645144'.

    Коды выдаёт каждый вуз сам, поэтому они пересекаются: без префикса
    абитуриент СПбГУ с кодом 1645144 и абитуриент МГУ с тем же кодом склеились
    бы в одну строку, а Монте-Карло посчитал бы их одним человеком, который
    может занять место только в одном вузе.
    """
    return f"{normalize_university(university)}:{(raw_id or '').strip()}"


def raw_applicant_id(key: str) -> str:
    """'hse:1645144' → '1645144'. Ключ без префикса возвращаем как есть."""
    uni, _, rest = (key or "").partition(":")
    return rest if rest and uni.lower() in UNIVERSITY_LABELS else key


def university_of_applicant(key: str) -> str | None:
    """'hse:1645144' → 'hse'. Ключ без известного префикса → None."""
    prefix = (key or "").split(":", 1)[0].strip().lower()
    return prefix if prefix in UNIVERSITY_LABELS else None


def candidate_applicant_keys(raw_code: str) -> list[str]:
    """
    Что искать в базе по коду, который ввёл человек.

    Один и тот же код может существовать в нескольких вузах (это разные люди),
    поэтому проверяем все префиксы. Сам код без префикса тоже оставляем:
    так продолжают находиться записи из снапшотов, собранных до разделения по
    вузам.
    """
    code = (raw_code or "").strip()
    if not code:
        return []
    if university_of_applicant(code):
        return [code]  # уже полный ключ, напр. 'hse:1645144'
    return [applicant_key(u, code) for u in SUPPORTED_UNIVERSITIES] + [code]


def split_codes(raw: str) -> list[str]:
    """
    Строка из поля ввода → список кодов абитуриента.

    Коды разных вузов у одного человека разные, поэтому поле принимает
    несколько — через запятую или с новой строки. По пробелу НЕ режем: СНИЛС
    пишут как «123-456-789 00», и такой код развалился бы надвое.
    """
    parts = [p.strip() for p in re.split(r"[,;\n\r]+", raw or "")]
    return list(dict.fromkeys(p for p in parts if p))
