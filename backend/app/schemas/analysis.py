"""LessonAnalysis — the structured output of the multimodal LLM analysis.

See docs/CONTRACT.md §5. This is the single source of truth for the analysis JSON. The LLM provider
must return JSON that validates against `LessonAnalysis`; `LessonAnalysis.model_json_schema()` is
what we hand to the model as the target schema.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

EventKind = Literal[
    "explanation",
    "task",
    "student_answer",
    "student_error",
    "re_explanation",
    "check",
    "visual_material",
    "off_topic",
    "other",
]
Speaker = Literal["teacher", "student", "both"]
SkillStatusLiteral = Literal["struggling", "developing", "mastered"]
Priority = Literal["high", "medium", "low"]
Relevance = Literal["high", "medium", "low"]
SignalStrength = Literal["strong", "useful", "weak"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Objective(_Strict):
    text: str = Field(description="Учебная цель урока, как её можно вывести из занятия")
    was_checked: bool = Field(description="Проверил ли преподаватель достижение цели наблюдаемым способом")
    evidence: str | None = Field(default=None, description="Короткое обоснование с таймкодом")


class Evidence(_Strict):
    timestamp: float = Field(ge=0, description="Секунда от начала записи")
    description: str
    quote: str | None = Field(default=None, description="Дословная реплика из транскрипта, если есть")
    frame_ids: list[int] = Field(default_factory=list, description="Индексы кадров (frames.index), подтверждающих наблюдение")


class TeachingEvent(_Strict):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    kind: EventKind
    speaker: Speaker
    summary: str
    quote: str | None = None
    frame_ids: list[int] = Field(default_factory=list)


class StudentError(_Strict):
    timestamp: float = Field(ge=0)
    skill_key: str = Field(description="Латинский slug навыка, устойчивый между уроками")
    skill_name: str = Field(description="Название навыка по-русски")
    description: str
    quote: str | None = None
    frame_ids: list[int] = Field(default_factory=list)
    repeated_after_explanation: bool = Field(
        default=False, description="Ошибка повторилась после того, как преподаватель уже объяснял это правило"
    )
    corrected: bool = Field(default=False, description="Ученик сам или с подсказкой исправил ошибку в ходе урока")


class Misconception(_Strict):
    skill_key: str
    description: str
    confidence: float = Field(ge=0, le=1)
    signal_strength: SignalStrength = Field(
        description="strong — повтор ошибки / не может обосновать; useful — прямой вопрос ученика; weak — паузы, заминки"
    )
    evidence: list[Evidence] = Field(default_factory=list)


class SkillAssessment(_Strict):
    skill_key: str
    skill_name: str
    status: SkillStatusLiteral = Field(
        description="mastered только при наблюдаемых самостоятельных правильных ответах; иначе developing/struggling"
    )
    evidence: list[Evidence] = Field(default_factory=list)


class VisualObservation(_Strict):
    frame_id: int = Field(description="frames.index")
    timestamp: float = Field(ge=0)
    description: str = Field(description="Что показано: доска, учебник, упражнение, запись ученика, чат, ничего полезного")
    text_detected: str | None = Field(default=None, description="Текст, который видно на кадре (как есть, включая ошибки)")
    relevance: Relevance
    issue: str | None = Field(default=None, description="Ошибка в написанном, несоответствие сказанному, нечитаемо и т.п.")


class TeacherRecommendation(_Strict):
    timestamp: float | None = None
    observation: str = Field(description="Что произошло (конкретно, проверяемо)")
    suggestion: str = Field(description="Что сделать иначе; желательно с готовой формулировкой вопроса ученику")
    priority: Priority = "medium"


class ManagerIndicators(_Strict):
    objectives_checked: bool
    difficulties_detected: bool
    re_explanation_happened: bool
    needs_methodist_review: bool
    review_reason: str | None = None
    teacher_talk_share: float | None = Field(default=None, ge=0, le=1)
    student_talk_share: float | None = Field(default=None, ge=0, le=1)
    tasks_total: int = Field(default=0, ge=0)
    tasks_solved_independently: int = Field(default=0, ge=0)
    lesson_structure_notes: str = ""


class ParentReportContent(_Strict):
    topic_plain: str = Field(description="Тема простыми словами, без терминов, 1–2 предложения")
    what_child_did_well: list[str] = Field(default_factory=list)
    what_was_hard: list[str] = Field(default_factory=list)
    what_to_repeat: list[str] = Field(default_factory=list, description="Что повторить дома, конкретно и выполнимо за 10–15 минут")
    home_task_suggestion: str | None = None
    encouragement: str = Field(description="Одна поддерживающая фраза, без оценок преподавателя")
    progress_note: str | None = Field(default=None, description="Связь с прошлыми уроками, если есть история")


class LessonAnalysis(_Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    subject: str = "Русский язык"
    topic: str
    lesson_summary: str
    learning_objectives: list[Objective] = Field(default_factory=list)
    timeline: list[TeachingEvent] = Field(default_factory=list)
    independent_successes: list[Evidence] = Field(default_factory=list)
    errors: list[StudentError] = Field(default_factory=list)
    misconceptions: list[Misconception] = Field(default_factory=list)
    skills: list[SkillAssessment] = Field(default_factory=list)
    visual_observations: list[VisualObservation] = Field(default_factory=list)
    teacher_recommendations: list[TeacherRecommendation] = Field(default_factory=list)
    manager_indicators: ManagerIndicators
    parent_report: ParentReportContent
    limitations: list[str] = Field(default_factory=list)
    language_notes: str | None = None


# Starter skill vocabulary for Russian-as-a-second-language lessons in Kazakh schools.
# The LLM may add new keys; these anchor consistency across lessons.
RUSSIAN_SKILL_KEYS: dict[str, str] = {
    "padezhnye_okonchaniya": "Падежные окончания имён существительных",
    "rod_sushchestvitelnyh": "Род имён существительных",
    "soglasovanie_prilagatelnyh": "Согласование прилагательных с существительными",
    "glagoly_vid": "Вид глагола (совершенный / несовершенный)",
    "glagoly_vremya": "Времена глагола",
    "glagoly_dvizheniya": "Глаголы движения",
    "tsya_tsa": "Правописание -тся / -ться",
    "bezudarnye_glasnye": "Безударные гласные в корне",
    "udarenie": "Ударение в словах",
    "zhi_shi": "Правописание жи-ши, ча-ща, чу-щу",
    "myagkiy_znak": "Мягкий знак (Ь) и твёрдый знак (Ъ)",
    "predlogi": "Употребление предлогов",
    "poryadok_slov": "Порядок слов в предложении",
    "chislitelnye": "Склонение числительных",
    "leksika_tema": "Лексика по теме урока",
    "chtenie_ponimanie": "Чтение и понимание текста",
    "ustnaya_rech": "Устная речь: построение связного высказывания",
    "proiznoshenie": "Произношение (ы/и, мягкие согласные, редукция гласных)",
    "punktuatsiya": "Пунктуация в простом предложении",
}

# Starter vocabularies by lesson subject code. Subjects without an entry start empty: the LLM creates keys,
# and the student's own keys for that subject are fed back on later lessons.
STARTER_SKILL_KEYS: dict[str, dict[str, str]] = {"russian": RUSSIAN_SKILL_KEYS}
