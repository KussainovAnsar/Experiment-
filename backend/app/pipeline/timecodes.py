"""Re-anchor the model's timecodes to the transcript after the analysis comes back.

The transcript is shown to the model as ``[mm:ss]`` lines while the schema wants seconds; weaker models copy
the label as a number (``[02:52]`` → ``2.52``) or answer in minutes. The transcript itself is the ground truth,
so every quote is looked up in it (fuzzy, by words, across segment borders) and its timestamp becomes the start
of the segment where the quote begins. Items without a findable quote fall back to a unit conversion learnt
from the matched ones (seconds / ``MM.SS`` / minutes) and are clamped to the lesson duration. Visual
observations take the timestamp of their frame. Report timecodes are rendered from these fields, so they agree.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from app.logging import get_logger
from app.schemas.analysis import Evidence, LessonAnalysis, StudentError

log = get_logger("app.pipeline.timecodes")

Unit = Literal["seconds", "mmss", "minutes"]

_WORD_RE = re.compile(r"\w+", re.UNICODE)
MIN_COVERAGE = 0.6  # share of the quote's words that must be found, in order, near each other
UNIT_TOLERANCE_SEC = 20.0  # a matched quote "votes" for a unit only if that unit lands within this distance
WINDOW_SLACK = 3  # extra transcript words allowed inside a quote window (fillers, recognition noise)


def _words(text: str | None) -> list[str]:
    return _WORD_RE.findall((text or "").lower().replace("ё", "е"))


def mmss_to_seconds(value: float) -> float | None:
    """``2.52`` read as ``02:52`` → ``172``; ``None`` when the fractional part cannot be seconds (≥ .60)."""
    minutes = math.floor(value)
    secs = round((value - minutes) * 100, 2)
    if secs >= 60:
        return None
    return minutes * 60 + secs


def convert(value: float, unit: Unit) -> float:
    if unit == "mmss":
        sec = mmss_to_seconds(value)
        return sec if sec is not None else value * 60
    if unit == "minutes":
        return value * 60
    return value


@dataclass(slots=True)
class TranscriptIndex:
    """All transcript words in one stream, each knowing the start of its segment."""

    words: list[str] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)

    @classmethod
    def build(cls, segments: Iterable[Mapping]) -> TranscriptIndex:
        idx = cls()
        for seg in segments:
            start = float(seg.get("start") or 0.0)
            for w in _words(str(seg.get("text") or "")):
                idx.words.append(w)
                idx.starts.append(start)
        return idx

    def find(self, quote: str | None, hint: float | None = None) -> float | None:
        """Start (s) of the segment where ``quote`` begins, or ``None`` when it is not in the transcript."""
        q = _words(quote)
        if not q or not self.words:
            return None
        anchors = {w for w in q if len(w) >= 3} or set(q)
        need = 1.0 if len(q) <= 2 else MIN_COVERAGE
        span = len(q) + WINDOW_SLACK
        best: tuple[float, float, float] | None = None  # (-coverage, distance to hint, start)
        seen: set[int] = set()
        for p, w in enumerate(self.words):
            if w not in anchors:
                continue
            lo = max(0, p - q.index(w))
            if lo in seen:
                continue
            seen.add(lo)
            window = self.words[lo : lo + span]
            blocks = [b for b in SequenceMatcher(None, q, window, autojunk=False).get_matching_blocks() if b.size]
            if not blocks:
                continue
            matched = sum(b.size for b in blocks)
            coverage = matched / len(q)
            if coverage < need:
                continue
            start = self.starts[lo + blocks[0].b]
            dist = abs(start - hint) if hint is not None else 0.0
            key = (-round(coverage, 2), dist, start)
            if best is None or key < best:
                best = key
        return best[2] if best else None


@dataclass(slots=True)
class RealignStats:
    unit: Unit = "seconds"
    matched: int = 0
    unmatched: int = 0
    changed: int = 0


def _detect_unit(pairs: list[tuple[float, float]], values: list[float], duration: float | None) -> Unit:
    """Which unit the model used: by matched quotes when there are any, else by the value range."""
    votes: dict[Unit, int] = {"seconds": 0, "mmss": 0, "minutes": 0}
    for value, truth in pairs:
        errs: dict[Unit, float] = {"seconds": abs(value - truth), "minutes": abs(value * 60 - truth)}
        mm = mmss_to_seconds(value)
        if mm is not None:
            errs["mmss"] = abs(mm - truth)
        unit = min(errs, key=lambda u: (errs[u], u != "seconds"))
        if errs[unit] <= UNIT_TOLERANCE_SEC:
            votes[unit] += 1
    if any(votes.values()):
        top = max(votes.values())
        for unit in ("seconds", "mmss", "minutes"):  # ties resolve to "the model was right"
            if votes[unit] == top:
                return unit  # type: ignore[return-value]
    positive = [v for v in values if v > 0]
    if duration and duration >= 180 and positive and max(positive) <= duration / 60 + 1:
        return "mmss" if all(mmss_to_seconds(v) is not None for v in positive) else "minutes"
    return "seconds"


def realign_timestamps(
    analysis: LessonAnalysis,
    segments: Iterable[Mapping],
    *,
    duration_sec: float | None = None,
    frame_times: Mapping[int, float] | None = None,
) -> RealignStats:
    """Fix every timestamp of ``analysis`` in place (see the module docstring)."""
    segments = list(segments or [])
    index = TranscriptIndex.build(segments)
    frame_times = dict(frame_times or {})
    if not duration_sec:
        duration_sec = max((float(s.get("end") or s.get("start") or 0.0) for s in segments), default=0.0) or None

    quoted: list[Evidence | StudentError] = [
        *analysis.independent_successes,
        *analysis.errors,
        *(e for m in analysis.misconceptions for e in m.evidence),
        *(e for s in analysis.skills for e in s.evidence),
    ]
    found: dict[int, float | None] = {id(it): index.find(it.quote) for it in quoted}
    found_events = {id(ev): index.find(ev.quote) for ev in analysis.timeline}

    pairs = [(it.timestamp, found[id(it)]) for it in quoted if found[id(it)] is not None]
    pairs += [(ev.start, found_events[id(ev)]) for ev in analysis.timeline if found_events[id(ev)] is not None]
    values = [it.timestamp for it in quoted] + [x for ev in analysis.timeline for x in (ev.start, ev.end)]
    values += [v.timestamp for v in analysis.visual_observations]
    values += [r.timestamp for r in analysis.teacher_recommendations if r.timestamp is not None]
    stats = RealignStats(unit=_detect_unit(pairs, values, duration_sec))  # type: ignore[arg-type]

    def clamp(x: float) -> float:
        x = max(0.0, x)
        return round(min(x, duration_sec) if duration_sec else x, 2)

    def fallback(x: float) -> float:
        return clamp(convert(x, stats.unit))

    unmatched_quotes: list[str] = []
    for it in quoted:
        old = it.timestamp
        truth = found[id(it)]
        if truth is not None:
            # a repeated phrase: prefer the occurrence nearest to what the model meant
            hinted = index.find(it.quote, hint=fallback(old))
            it.timestamp = clamp(truth if hinted is None else hinted)
            stats.matched += 1
        else:
            it.timestamp = fallback(old)
            if it.quote:
                stats.unmatched += 1
                unmatched_quotes.append(it.quote[:60])
        stats.changed += it.timestamp != old

    for ev in analysis.timeline:
        old = (ev.start, ev.end)
        start, end = fallback(ev.start), fallback(ev.end)
        truth = found_events[id(ev)]
        if truth is not None:
            hinted = index.find(ev.quote, hint=start)
            truth = clamp(truth if hinted is None else hinted)
            stats.matched += 1
            if not (start - UNIT_TOLERANCE_SEC <= truth <= end + UNIT_TOLERANCE_SEC):
                start = truth
        elif ev.quote:
            stats.unmatched += 1
            unmatched_quotes.append(ev.quote[:60])
        ev.start, ev.end = start, max(start, end)
        stats.changed += (ev.start, ev.end) != old

    for v in analysis.visual_observations:
        old = v.timestamp
        v.timestamp = clamp(frame_times[v.frame_id]) if v.frame_id in frame_times else fallback(old)
        stats.changed += v.timestamp != old

    for r in analysis.teacher_recommendations:
        if r.timestamp is not None:
            old = r.timestamp
            r.timestamp = fallback(old)
            stats.changed += r.timestamp != old

    if unmatched_quotes:
        log.warning(
            "analysis_quotes_not_in_transcript",
            count=len(unmatched_quotes),
            unit=stats.unit,
            quotes=unmatched_quotes[:5],
        )
    if stats.changed:
        log.info(
            "analysis_timecodes_realigned",
            unit=stats.unit,
            matched=stats.matched,
            unmatched=stats.unmatched,
            changed=stats.changed,
        )
    return stats
