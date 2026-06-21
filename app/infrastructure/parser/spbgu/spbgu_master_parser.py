# app/infrastructure/parser/spbgu/spbgu_master_parser.py
from __future__ import annotations

from typing import List, Tuple

from app.domain.models import Application, SubmissionStats
from app.infrastructure.parser.base import IApplicationsParser

# ВНИМАНИЕ. Это каркас, ожидающий результатов Фазы 0 (Selenium-разведка
# cabinet.spbu.ru/Lists/AG_Rating/, магистратура), которую невозможно выполнить
# из текущего окружения (исходящая сеть закрыта). Как только формат списков
# снят (селекторы навигации, заголовки колонок, идентификатор абитуриента,
# расположение КЦП и времени формирования), реализуем parse() по образцу
# app/infrastructure/parser/master_applications_parser.py:
#   • резолв колонок по нормализованным заголовкам (_header_map/_col_index);
#   • устойчивость к перерисовке таблицы (StaleElementReference, ретраи);
#   • маппинг строки → Application:
#       applicant_id  ← уникальный код СПбГУ / СНИЛС
#       total_score   ← сумма конкурсных баллов
#       vi_score      ← баллы ВИ
#       id_achievements ← ИД
#       priority      ← приоритет
#       consent       ← согласие (+/−)
#       review_status ← статус рассмотрения
#       subject1_score / subject2_score / target_id_achievements ← 0 (нет у СПбГУ)
#   • SubmissionStats ← КЦП (num_places) и время формирования (generated_at).


class SpbguMasterApplicationsParser(IApplicationsParser):
    """
    Парсер одного рейтингового списка магистратуры СПбГУ.

    Каркас: разметка/селекторы фиксируются после Selenium-разведки портала
    cabinet.spbu.ru (Фаза 0). До этого parse() осознанно бросает
    NotImplementedError, чтобы пайплайн не делал вид, что данные получены.
    """

    BASE_URL = "https://cabinet.spbu.ru/Lists/AG_Rating/"

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._driver = None  # WebDriver создаём лениво при первом parse()

    def parse(self, program_code: str) -> Tuple[SubmissionStats, List[Application]]:
        raise NotImplementedError(
            "Парсер СПбГУ ещё не реализован: нужна Selenium-разведка формата "
            "cabinet.spbu.ru (Фаза 0). См. комментарий в начале модуля."
        )

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            finally:
                self._driver = None
