"""Model timecodes are re-anchored to the transcript (quotes) and to frames."""

from __future__ import annotations

from app.pipeline.reports import fmt_ts
from app.pipeline.timecodes import TranscriptIndex, mmss_to_seconds, realign_timestamps
from app.schemas.analysis import LessonAnalysis

SEGMENTS = [
    {"start": 0.0, "end": 5.52, "text": "Посмотри на него внимательно, какие твои предположения?"},
    {"start": 5.52, "end": 20.22, "text": "после этого я тебе дам одну подсказку"},
    {"start": 26.76, "end": 37.2, "text": "получается 5x3 кубе именно 25"},
    {"start": 172.05, "end": 173.05, "text": "Здесь что осталось?"},
    {"start": 173.51, "end": 173.93, "text": "0."},
    {"start": 174.39, "end": 175.15, "text": "Пиши 0."},
    {"start": 264.98, "end": 277.18, "text": "двойку то же самое нужно сделать и с многочленом"},
    {"start": 349.79, "end": 350.64, "text": "уже как по массу."},
    {"start": 380.0, "end": 389.49, "text": "Пиши 0."},
]


def _analysis(**over) -> LessonAnalysis:
    base = {
        "topic": "Деление многочленов",
        "lesson_summary": "…",
        "manager_indicators": {
            "objectives_checked": False,
            "difficulties_detected": True,
            "re_explanation_happened": True,
            "needs_methodist_review": False,
        },
        "parent_report": {"topic_plain": "…", "encouragement": "…"},
    }
    base.update(over)
    return LessonAnalysis.model_validate(base)


def _error(ts: float, quote: str | None) -> dict:
    return {"timestamp": ts, "skill_key": "k", "skill_name": "Навык", "description": "d", "quote": quote}


def test_mmss_to_seconds() -> None:
    assert mmss_to_seconds(2.52) == 172
    assert mmss_to_seconds(5.2) == 320
    assert mmss_to_seconds(0.27) == 27
    assert mmss_to_seconds(1.75) is None


def test_quote_found_across_segments_and_punctuation() -> None:
    idx = TranscriptIndex.build(SEGMENTS)
    assert idx.find("здесь, что осталось? 0") == 172.05
    assert idx.find("ПОЛУЧАЕТСЯ 5x3 кубе именно") == 26.76
    assert idx.find("какие-то слова которых нет вообще") is None
    # repeated phrase: the occurrence nearest to the hint wins
    assert idx.find("Пиши 0.", hint=170) == 174.39
    assert idx.find("Пиши 0.", hint=385) == 380.0


def test_mmss_answer_is_realigned_and_report_timecode_matches() -> None:
    a = _analysis(
        errors=[_error(2.52, "Здесь что осталось? 0."), _error(4.37, "то же самое нужно сделать с многочленом")],
        skills=[
            {
                "skill_key": "k",
                "skill_name": "Навык",
                "status": "developing",
                "evidence": [{"timestamp": 0.27, "description": "d", "quote": "получается 5x3 кубе именно 25"}],
            }
        ],
        timeline=[
            {
                "start": 0.0,
                "end": 0.27,
                "kind": "explanation",
                "speaker": "teacher",
                "summary": "s",
                "quote": "посмотри на него внимательно",
            },
            {
                "start": 5.52,
                "end": 6.33,
                "kind": "task",
                "speaker": "both",
                "summary": "s",
                "quote": "уже как по массу",
            },
        ],
        visual_observations=[
            {"frame_id": 7, "timestamp": 3.12, "description": "d", "relevance": "high"},
            {"frame_id": 99, "timestamp": 3.12, "description": "d", "relevance": "high"},
        ],
        teacher_recommendations=[
            {"timestamp": 5.2, "observation": "o", "suggestion": "s"},
            {"timestamp": None, "observation": "o", "suggestion": "s"},
        ],
        independent_successes=[{"timestamp": 1.15, "description": "без цитаты"}],
    )
    stats = realign_timestamps(a, SEGMENTS, duration_sec=390, frame_times={7: 190.0})
    assert stats.unit == "mmss" and stats.matched == 5 and stats.unmatched == 0
    assert [e.timestamp for e in a.errors] == [172.05, 264.98]
    assert a.skills[0].evidence[0].timestamp == 26.76
    assert (a.timeline[0].start, a.timeline[0].end) == (0.0, 27.0)
    assert (a.timeline[1].start, a.timeline[1].end) == (352.0, 390.0)  # end clamped to the duration
    assert a.visual_observations[0].timestamp == 190.0  # the frame's own time
    assert a.visual_observations[1].timestamp == 192.0  # unknown frame → unit fallback 3:12
    assert a.teacher_recommendations[0].timestamp == 320.0 and a.teacher_recommendations[1].timestamp is None
    assert a.independent_successes[0].timestamp == 75.0
    assert fmt_ts(a.errors[0].timestamp) == "02:52"


def test_correct_seconds_are_kept_and_unknown_quote_is_clamped(caplog) -> None:
    a = _analysis(
        errors=[
            _error(173.0, "Здесь что осталось"),
            _error(30.0, "получается 5x3"),
            _error(1000.0, "этой фразы в транскрипте нет совсем никак"),
        ]
    )
    stats = realign_timestamps(a, SEGMENTS, duration_sec=390)
    assert stats.unit == "seconds" and stats.unmatched == 1
    assert [e.timestamp for e in a.errors] == [172.05, 26.76, 390.0]


def test_no_matches_small_values_on_long_lesson_read_as_mmss() -> None:
    a = _analysis(errors=[_error(2.52, None), _error(5.2, None)])
    stats = realign_timestamps(a, SEGMENTS, duration_sec=390)
    assert stats.unit == "mmss"
    assert [e.timestamp for e in a.errors] == [172.0, 320.0]


def test_no_matches_plausible_seconds_untouched() -> None:
    a = _analysis(errors=[_error(12.0, None), _error(300.0, None)])
    assert realign_timestamps(a, SEGMENTS, duration_sec=390).unit == "seconds"
    assert [e.timestamp for e in a.errors] == [12.0, 300.0]
