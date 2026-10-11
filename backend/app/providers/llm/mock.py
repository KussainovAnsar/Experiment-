"""Mock LLM provider used in tests and demos.

Returns ``demo/lesson_01/analysis.json`` (module E fixture, validated against ``LessonAnalysis``) when it
exists, otherwise :func:`fallback_analysis` — a realistic, fully valid analysis of a short Russian lesson
(dative case endings, -тся/-ться) that matches the mock STT fallback transcript. Neither the transcript nor
the images are inspected. Override the fixture path with ``MOCK_ANALYSIS_PATH``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pydantic import ValidationError

from app.config import REPO_DIR
from app.logging import get_logger
from app.providers.llm.base import ImageInput, LLMResult
from app.schemas.analysis import (
    Evidence,
    LessonAnalysis,
    ManagerIndicators,
    Misconception,
    Objective,
    ParentReportContent,
    SkillAssessment,
    StudentError,
    TeacherRecommendation,
    TeachingEvent,
    VisualObservation,
)

log = get_logger("app.providers.llm.mock")

DEFAULT_FIXTURE = REPO_DIR / "demo" / "lesson_01" / "analysis.json"


def fixture_path() -> Path:
    """Path of the analysis fixture (env override or the demo default)."""
    return Path(os.environ.get("MOCK_ANALYSIS_PATH") or DEFAULT_FIXTURE)


def load_fixture(path: Path) -> LessonAnalysis:
    """Load and validate an ``analysis.json`` fixture."""
    return LessonAnalysis.model_validate(json.loads(path.read_text(encoding="utf-8")))


def fallback_analysis() -> LessonAnalysis:
    """Built-in analysis of the mock lesson (independent of the demo fixtures).

    Lesson plot (see ``app.providers.stt.mock``): the teacher revises dative endings — the student first
    says «к школа», self-corrects to «к школе»; then writes «Он учиться в пятом классе» (wrong -ться), the
    teacher re-explains the «что делает?» check and the student repeats the rule correctly.
    """
    return LessonAnalysis(
        subject="Русский язык",
        topic="Падежные окончания имён существительных; правописание -тся/-ться",
        lesson_summary=(
            "Короткое индивидуальное занятие: повторение дательного падежа и правописания -тся/-ться. "
            "Ученица сначала ошиблась в окончании («к школа»), но сама исправилась на «к школе». "
            "В письменном задании написала «учиться» вместо «учится»; преподаватель объяснил проверку "
            "вопросом «что делает?», ученица повторила правило. Самостоятельная проверка усвоения -тся/-ться "
            "на новом примере не проводилась."
        ),
        learning_objectives=[
            Objective(
                text="Правильно употреблять окончание дательного падежа существительных",
                was_checked=True,
                evidence="00:13 — ученица сама исправила «к школа» на «к школе»",
            ),
            Objective(
                text="Различать -тся и -ться по вопросу «что делает? / что делать?»",
                was_checked=False,
                evidence="00:31 — правило объяснено, но новый пример для самостоятельной проверки не дан",
            ),
        ],
        timeline=[
            TeachingEvent(
                start=0.0,
                end=4.5,
                kind="explanation",
                speaker="teacher",
                summary="Объявлена тема: повторение падежных окончаний",
                quote="Сегодня мы повторяем падежные окончания имён существительных.",
            ),
            TeachingEvent(
                start=7.0,
                end=13.0,
                kind="task",
                speaker="teacher",
                summary="Задание: закончить фразу «Я иду к … (школа)»",
            ),
            TeachingEvent(
                start=13.0,
                end=16.0,
                kind="student_answer",
                speaker="student",
                summary="Ответ с заминкой и самокоррекцией: «к школа… к школе»",
                quote="Я иду к школа... к школе.",
            ),
            TeachingEvent(
                start=16.0,
                end=22.0,
                kind="check",
                speaker="teacher",
                summary="Подтверждение ответа с опорой на казахский («мектепке — к школе»)",
            ),
            TeachingEvent(
                start=22.0,
                end=27.0,
                kind="task",
                speaker="teacher",
                summary="Письменное задание: написать «учится» в предложении",
                frame_ids=[1],
            ),
            TeachingEvent(
                start=27.0,
                end=31.0,
                kind="student_error",
                speaker="student",
                summary="Ученица пишет «учиться» (лишний мягкий знак)",
                quote="Он учиться в пятом классе.",
                frame_ids=[1],
            ),
            TeachingEvent(
                start=31.0,
                end=38.0,
                kind="re_explanation",
                speaker="teacher",
                summary="Переобъяснение: проверка вопросом «что делает?»",
                quote="Проверим вопросом: «что делает?» — учится, без мягкого знака.",
            ),
            TeachingEvent(
                start=38.0,
                end=41.0,
                kind="student_answer",
                speaker="student",
                summary="Ученица повторяет правило",
                quote="Поняла. Что делает — учится.",
            ),
        ],
        independent_successes=[
            Evidence(
                timestamp=13.0,
                description="Самостоятельно исправила окончание дательного падежа: «к школа» → «к школе»",
                quote="Я иду к школа... к школе.",
            ),
        ],
        errors=[
            StudentError(
                timestamp=27.0,
                skill_key="tsya_tsa",
                skill_name="Правописание -тся / -ться",
                description="Написала «учиться» вместо «учится» в предложении «Он учится в пятом классе»",
                quote="Он учиться в пятом классе.",
                frame_ids=[1],
                repeated_after_explanation=False,
                corrected=True,
            ),
        ],
        misconceptions=[
            Misconception(
                skill_key="tsya_tsa",
                description="Мягкий знак ставится по аналогии с инфинитивом, без проверки вопросом",
                confidence=0.6,
                signal_strength="useful",
                evidence=[
                    Evidence(
                        timestamp=27.0, description="Ошибка в письменном задании", quote="Он учиться в пятом классе."
                    ),
                ],
            ),
            Misconception(
                skill_key="padezhnye_okonchaniya",
                description="Заминка при выборе окончания дательного падежа (возможное влияние казахского)",
                confidence=0.3,
                signal_strength="weak",
                evidence=[Evidence(timestamp=13.0, description="Пауза и самокоррекция «к школа… к школе»")],
            ),
        ],
        skills=[
            SkillAssessment(
                skill_key="padezhnye_okonchaniya",
                skill_name="Падежные окончания имён существительных",
                status="developing",
                evidence=[
                    Evidence(timestamp=13.0, description="Самокоррекция «к школа» → «к школе» без подсказки"),
                ],
            ),
            SkillAssessment(
                skill_key="tsya_tsa",
                skill_name="Правописание -тся / -ться",
                status="struggling",
                evidence=[
                    Evidence(timestamp=27.0, description="Ошибка «учиться» вместо «учится»", frame_ids=[1]),
                    Evidence(
                        timestamp=38.0, description="Повторила правило после переобъяснения, но без нового примера"
                    ),
                ],
            ),
        ],
        visual_observations=[
            VisualObservation(
                frame_id=1,
                timestamp=27.0,
                description="Запись ученицы в тетради / на экране",
                text_detected="Он учиться в пятом классе.",
                relevance="high",
                issue="Лишний мягкий знак в «учиться»; в речи преподаватель произнёс «учится»",
            ),
        ],
        teacher_recommendations=[
            TeacherRecommendation(
                timestamp=38.0,
                observation=(
                    "После переобъяснения -тся/-ться ученица только повторила правило; новый пример не проверялся"
                ),
                suggestion=(
                    "Дать 2–3 новых предложения на выбор -тся/-ться и спросить: «Задай вопрос к глаголу — "
                    "что делает или что делать? Какой пишем?»"
                ),
                priority="high",
            ),
            TeacherRecommendation(
                timestamp=16.0,
                observation="Опора на казахский («мектепке») помогла закрепить дательный падеж",
                suggestion="Продолжать параллели с казахскими падежами, но попросить ученицу самой назвать аналог",
                priority="medium",
            ),
        ],
        manager_indicators=ManagerIndicators(
            objectives_checked=True,
            difficulties_detected=True,
            re_explanation_happened=True,
            needs_methodist_review=False,
            review_reason=None,
            teacher_talk_share=0.68,
            student_talk_share=0.32,
            tasks_total=2,
            tasks_solved_independently=1,
            lesson_structure_notes=(
                "Тема объявлена, два задания, одно переобъяснение. Отсутствует итоговая проверка нового правила."
            ),
        ),
        parent_report=ParentReportContent(
            topic_plain=(
                "Повторяли, как меняются окончания слов (к школе, к маме) "
                "и когда в глаголах пишется -тся, а когда -ться."
            ),
            what_child_did_well=[
                "Сама исправила окончание в фразе «я иду к школе»",
                "Повторила правило про -тся/-ться",
            ],
            what_was_hard=["В слове «учится» написала лишний мягкий знак («учиться»)"],
            what_to_repeat=[
                "Задать вопрос к глаголу: «что делает?» → учится, «что делать?» → учиться",
                "Составить 3 предложения со словами «к школе», «к бабушке», «к другу»",
            ],
            home_task_suggestion="Выписать из любого текста 5 глаголов на -тся/-ться и к каждому задать вопрос.",
            encouragement="Ребёнок замечает и исправляет свои ошибки сам — это важный шаг.",
            progress_note=None,
        ),
        limitations=[
            "Запись короткая: выводы о навыках основаны на одном–двух примерах.",
            "Разделение говорящих выполнено автоматически и может содержать ошибки.",
        ],
        language_notes=(
            "Ученица отвечает по-русски, отдельные реплики на казахском («Жақсы, түсіндім»); "
            "преподаватель использует казахские параллели."
        ),
    )


class MockLLMProvider:
    """Deterministic provider: fixture file if present and valid, else :func:`fallback_analysis`."""

    name = "mock"

    def __init__(self, fixture: Path | None = None) -> None:
        self._fixture = fixture

    def load(self) -> LessonAnalysis:
        """Return the analysis this provider would answer with."""
        path = self._fixture or fixture_path()
        if path.is_file():
            try:
                result = load_fixture(path)
                log.info("llm_mock_fixture", path=str(path))
                return result
            except (OSError, ValueError, ValidationError) as exc:
                log.warning("llm_mock_fixture_invalid", path=str(path), error=str(exc)[:300])
        log.info("llm_mock_fallback")
        return fallback_analysis()

    async def analyze(self, *, system: str, user_text: str, images: list[ImageInput], json_schema: dict) -> LLMResult:
        """Ignore the prompt content and return the fixture / fallback analysis."""
        t0 = time.monotonic()
        analysis = self.load()
        return LLMResult(
            data=analysis.model_dump(mode="json"),
            model="mock",
            tokens_in=len(system) // 4 + len(user_text) // 4,
            tokens_out=len(analysis.model_dump_json()) // 4,
            latency_ms=int((time.monotonic() - t0) * 1000),
            extra={"images": len(images)},
        )
