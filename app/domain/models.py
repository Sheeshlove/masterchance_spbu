import datetime
from dataclasses import dataclass


@dataclass
class Institute:
    """
    Институт: определяется по первым двум цифрам кода кафедры.
    Например, код кафедры '01.04.02' → institute_code='01'.
    """
    code: str  # '01'
    name: str  # название института


@dataclass
class Department:
    """
    Кафедра (department): код всей тройки цифр, напр. '01.04.02',
    name — текст между кодом и первой скобкой.
    """
    code: str  # '01.04.02'
    name: str  # 'Прикладная математика и информатика'
    institute_code: str  # ссылка на Institute.code


@dataclass
class Program:
    """
    Направление обучения (program):
    - code: внутренний числовой код, напр. '786'
    - name: текст в первой паре скобок
    - is_ino: пометка «(ИНО)»
    - is_international: содержит «международная образовательная программа»
    - department_code: ссылка на Department.code
    """
    code: str
    name: str
    department_code: str
    is_ino: bool = False
    is_international: bool = False
    university: str = "spbgu"  # источник данных; сейчас только СПбГУ


@dataclass
class SubmissionStats:
    """
    Сводка по направлению.
    """
    program_code: str  # FK → Program.code
    num_places: int  # из <span id="numPlaces">
    num_applications: int  # из <span id="numApplications">
    generated_at: datetime  # время формирования из <span id="date_info">


@dataclass
class Applicant:
    """
    Абитуриент.
    """
    id: str  # Уникальный код поступающего
    university: str = "spbgu"  # источник данных; сейчас только СПбГУ


@dataclass
class Application:
    """
    Заявка абитуриента по направлению.
    """
    program_code: str  # FK → Program.code
    applicant_id: str  # FK → Applicant.id
    total_score: int  # Сумма конкурсных баллов
    vi_score: int  # Сумма баллов за ВИ
    subject1_score: int  # Баллы по предмету 1
    subject2_score: int  # Баллы по предмету 2
    id_achievements: int  # Баллы за общие ИД (индивидуальные достижения)
    target_id_achievements: int  # Баллы за целевые ИД
    priority: int  # Приоритет
    consent: bool  # Наличие согласия (“+” → True)
    review_status: str  # Информация о рассмотрении


@dataclass
class ProgramPassingQuantile:
    """
    Квантиль 90 / 95 % проходного балла по направлению.
    """
    program_code: str
    q90: float
    q95: float


@dataclass
class AdmissionProbability:
    """
    Вероятность поступления абитуриента на конкретное направление.
    """
    applicant_id: str
    program_code: str
    probability: float


@dataclass
class ProgramCompetition:
    """
    Расклад конкурса на направлении глазами конкретного абитуриента.

    Сам по себе шанс из Монте-Карло — «чёрный ящик»: пользователь видит 37%
    и не понимает, откуда они. Эти поля — те же факты, из которых модель
    считает, только в человеческом виде: сколько мест, сколько соперников
    сильнее, сколько идут с нулём, сколько конкурентов сидят без согласия
    (именно их модель разыгрывает на уход в другие вузы).
    """
    program_code: str
    seats: int | None            # мест по данным вуза
    applications: int            # всего заявок в базе
    scored_rivals: int           # соперников с уже известным баллом
    better: int                  # из них с баллом выше моего
    same: int                    # с баллом ровно как у меня
    unscored_rivals: int         # соперников без балла — в конкурсе идут с нулём
    rivals_without_consent: int  # соперников без согласия НИГДЕ — пул оттока MC
    my_priority: int | None
    my_total_score: int | None   # 0/None → балла в списках нет
    my_consent: bool


@dataclass
class AdmissionDiagnostics:
    """
    Диагностика для абитуриента по результатам MC:
    - p_excluded: доля симуляций, где он «выпал» (opt-out)
    - p_fail_when_included: доля провалов (никуда не поступил) среди тех симуляций, где он был включён.
    """
    applicant_id: str
    p_excluded: float
    p_fail_when_included: float

@dataclass
class ExamSession:
    """
    Дата/время вступительного испытания, привязанная к нашей программе.
    Время в час. по Москве, tz-naive (как submission_stats).
    """
    program_code: str            # FK -> programs.code (наш внутренний код)
    exam_code: str | None        # "Код" со страницы, напр. "38.04.05_06"
    dt: datetime.datetime        # дата и время экзамена
    institute: str | None = None
    education_form: str | None = None  # Очная / Заочная
    contract: str | None = None        # Бюджет / Контракт
    program_name: str | None = None    # как на сайте расписания
    program_pdf_url: str | None = None