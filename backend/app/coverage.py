"""校注覆盖盲区统计引擎（annotation coverage gaps）。

基于**已保存**的转写片段与校注，按说话人统计实际发言时长、被校注覆盖
时长与覆盖率，并列出每位说话人尚未被覆盖的发言区间（盲区）。

统计规则（与编辑页「发言时长」面板一致，全部使用半开区间与毫秒整数）：

1. 忽略空 / 纯空白说话人以及零长度或非法（``end <= start``、时间非数字）
   的片段与校注；时间由秒换算为毫秒时四舍五入（与前端 ``Math.round`` 一致）。
2. 同一说话人自身重叠或端点相接的发言片段先合并，保证同一毫秒不会因片段
   重叠而给同一说话人重复累加。
3. **仅同名说话人的校注可覆盖其发言**：把该说话人的全部校注先做并集
   （重叠校注不重复计时），再与其发言区间取交集——校注超出发言范围的部分
   （越界、覆盖空隙、延伸到他人发言的部分）一律不计入覆盖。
4. 发言区间减去覆盖并集，余下的即该说话人的**未覆盖盲区**。多人同时发言
   时各自独立统计：A 的发言被 A 的校注覆盖，与 B 是否在场、是否有校注无关。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def _to_ms(value: Any) -> int | None:
    """Convert seconds to a non-negative integer millisecond value.

    Rounding is half-up (``2.675s -> 2675ms``), mirroring the frontend
    ``Math.round(Number(x) * 1000)``: Python's shortest ``repr`` of a float
    is the same string JavaScript's ``Number.toString`` produces.
    """
    if isinstance(value, bool):
        return None
    try:
        text = str(value).strip()
        dec = Decimal(text)
    except (ValueError, ArithmeticError, TypeError):
        return None
    if not dec.is_finite():
        return None
    ms = int((dec * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return max(0, ms)


def _speaker(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _valid_intervals(items: list[dict]) -> list[dict]:
    """Keep items with a non-empty speaker and valid positive-length bounds."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        start, end = _to_ms(it.get("start")), _to_ms(it.get("end"))
        if start is None or end is None or end <= start:
            continue
        name = _speaker(it.get("speaker"))
        if not name:
            continue
        out.append({"start": start, "end": end, "speaker": name})
    return out


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or touching half-open [a, b) integer ranges."""
    out: list[list[int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _length(ranges: list[tuple[int, int]]) -> int:
    return sum(b - a for a, b in ranges)


def _intersect_union(a_ranges: list[tuple[int, int]],
                     b_ranges: list[tuple[int, int]]
                     ) -> list[tuple[int, int]]:
    """Intersection of two merged range unions (then re-merged)."""
    out: list[tuple[int, int]] = []
    for a0, a1 in a_ranges:
        for b0, b1 in b_ranges:
            s, e = max(a0, b0), min(a1, b1)
            if e > s:
                out.append((s, e))
    return _merge(out)


def _subtract_ranges(base: list[tuple[int, int]],
                     cover: list[tuple[int, int]]
                     ) -> list[tuple[int, int]]:
    """Parts of the merged ``base`` union not covered by merged ``cover``."""
    out: list[tuple[int, int]] = []
    j = 0
    n = len(cover)
    for a, b in base:
        cursor = a
        # skip cover intervals wholly before this piece (half-open touching
        # counts as before); a cover straddling the piece start stays.
        while j < n and cover[j][1] <= a:
            j += 1
        k = j
        while k < n and cover[k][0] < b:
            c0, c1 = cover[k]
            if c0 > cursor:
                out.append((cursor, c0))
            if c1 > cursor:
                cursor = c1
            if cursor >= b:
                break
            k += 1
        if cursor < b:
            out.append((cursor, b))
    return out


def _sec(ms: int | float) -> float:
    return round(ms / 1000, 3)


def _gap(start: int, end: int) -> dict:
    return {"start": _sec(start), "end": _sec(end),
            "durationMs": end - start, "duration": _sec(end - start)}


def coverage_report(document: dict | None,
                    speaker: str | None = None) -> dict:
    """Compute per-speaker speech / covered durations and uncovered gaps.

    When ``speaker`` is given (exact, trimmed name) only that speaker is
    reported; an unknown name yields an empty speaker list.
    """
    document = document or {}
    segments = _valid_intervals(document.get("segments", []))
    annotations = _valid_intervals(document.get("annotations", []))

    # Speaker order follows first appearance among valid saved segments.
    speech_raw: dict[str, list[tuple[int, int]]] = {}
    for it in segments:
        speech_raw.setdefault(it["speaker"], []).append((it["start"], it["end"]))

    annotations_raw: dict[str, list[tuple[int, int]]] = {}
    for it in annotations:
        annotations_raw.setdefault(it["speaker"], []).append(
            (it["start"], it["end"]))

    names = list(speech_raw.keys())
    if speaker is not None:
        key = speaker.strip()
        names = [key] if key in speech_raw else []

    speakers: list[dict] = []
    total_speech = total_covered = total_gaps = 0
    total_gap_count = 0

    for name in names:
        speech = _merge(speech_raw[name])
        # Same-speaker annotations unioned first (overlaps never count twice),
        # then clipped to this speaker's speech: parts beyond speech are dropped.
        covering = _merge(annotations_raw.get(name, []))
        covered = _intersect_union(speech, covering)
        gaps = _subtract_ranges(speech, covered)

        speech_ms = _length(speech)
        covered_ms = _length(covered)
        uncovered_ms = speech_ms - covered_ms
        gap_count = len(gaps)

        total_speech += speech_ms
        total_covered += covered_ms
        total_gaps += uncovered_ms
        total_gap_count += gap_count

        speakers.append({
            "speaker": name,
            "speechMs": speech_ms,
            "speechSeconds": _sec(speech_ms),
            "coveredMs": covered_ms,
            "coveredSeconds": _sec(covered_ms),
            "uncoveredMs": uncovered_ms,
            "uncoveredSeconds": _sec(uncovered_ms),
            "coverage": round(covered_ms / speech_ms, 4)
            if speech_ms else 0.0,
            "speechRanges": [{"start": _sec(a), "end": _sec(b)}
                             for a, b in speech],
            "uncoveredCount": gap_count,
            "uncoveredRanges": [_gap(a, b) for a, b in gaps],
        })

    return {
        "summary": {
            "speakers": len(speakers),
            "speechMs": total_speech,
            "speechSeconds": _sec(total_speech),
            "coveredMs": total_covered,
            "coveredSeconds": _sec(total_covered),
            "uncoveredMs": total_gaps,
            "uncoveredSeconds": _sec(total_gaps),
            "coverage": round(total_covered / total_speech, 4)
            if total_speech else 0.0,
            "uncoveredIntervalCount": total_gap_count,
        },
        "speakers": speakers,
    }
