"""摘录包引擎（excerpt package）。

用户给出若干起止时间区间（顺序不限、允许重叠），构建摘录包时先把**相交或
首尾相接**（允许 ``EPS`` 浮点容差）的输入区间合并成若干互不相连的合并区间，
再按合并区间收录内容，规则有两条：

1. **转写片段只收录完整落在合并区间内的**（半开区间 ``[start, end)``，
   ``seg.start >= lo`` 且 ``seg.end <= hi``）；与区间时间相交但跨界的片段
   （起于区间之前、终于区间之后，或两侧都伸出）整段列为**排除项**，绝不
   截取片段的一部分文本充作摘录——这保证摘录里的每一段都是未经篡改的完整
   转写片段；
2. **校注与合并区间相交时按边界裁剪**（相交长度为 0 的端点相接不产生校注）；
   一条校注跨越多个互不相连的合并区间时**拆成多条**，每条沿用裁剪后的
   起止时间，保留原校注 ID 作为来源标识 ``sourceAnnotationId``，其余字段
   （说话人、主题、备注、规则溯源等）原样保留。

整个过程只读项目文档，不写回任何数据。
"""
from __future__ import annotations

import math
from typing import Any

EPS = 1e-9

# 排除原因
REASON_CROSSES_START = "crosses_start"   # 片段起于区间之前（结尾探入区间）
REASON_CROSSES_END = "crosses_end"       # 片段终于区间之后（开头探入区间）
REASON_CROSSES_BOTH = "crosses_both"     # 片段在区间两侧都伸出
REASON_CROSSES_GAP = "crosses_gap"       # 片段跨过区间之间的空隙（跨界多个区间）


def _r3(value: float) -> float:
    return round(float(value) + 0.0, 3)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or math.isinf(out):  # NaN / ±inf
        return None
    return out


def normalize_ranges(raw_ranges: Any) -> list[tuple[float, float]]:
    """Validate the user-supplied ranges.

    Non-numeric / negative / non-finite endpoints or ``end <= start`` raise
    ValueError (turned into 400 by the endpoint).
    """
    if not isinstance(raw_ranges, list) or not raw_ranges:
        raise ValueError("请至少输入一个时间区间")
    ranges: list[tuple[float, float]] = []
    for i, raw in enumerate(raw_ranges):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {i + 1} 行区间格式不正确")
        start, end = _num(raw.get("start")), _num(raw.get("end"))
        if start is None or end is None:
            raise ValueError(f"第 {i + 1} 行区间的起止时间必须是数字")
        if start < 0 or end < 0:
            raise ValueError(f"第 {i + 1} 行区间的时间不能为负数")
        if end <= start:
            raise ValueError(f"第 {i + 1} 行区间的结束时间必须晚于开始时间")
        ranges.append((start, end))
    return ranges


def merge_inputs(ranges: list[tuple[float, float]]
                 ) -> list[dict[str, Any]]:
    """Merge overlapping / touching input ranges into disjoint intervals.

    Returns ``[{"start", "end", "sources": [(s, e), ...]}]`` sorted by time;
    ``sources`` keeps the original input rows absorbed by each merged
    interval (also sorted) so the preview can explain what got merged.
    """
    ordered = sorted(ranges, key=lambda r: (r[0], r[1]))
    merged: list[dict[str, Any]] = []
    for start, end in ordered:
        if merged and start <= merged[-1]["end"] + EPS:
            if end > merged[-1]["end"]:
                merged[-1]["end"] = end
            merged[-1]["sources"].append((start, end))
        else:
            merged.append({"start": start, "end": end,
                           "sources": [(start, end)]})
    out: list[dict[str, Any]] = []
    for index, item in enumerate(merged):
        sources = sorted(item["sources"])
        out.append({
            "index": index,
            "start": _r3(item["start"]),
            "end": _r3(item["end"]),
            "sources": [{"start": _r3(s), "end": _r3(e)} for s, e in sources],
        })
    return out


def _valid_segments(segments: list[dict]) -> list[dict]:
    """Keep well-formed segments, sorted by time (then original order)."""
    out = []
    for order, raw in enumerate(segments or []):
        if not isinstance(raw, dict):
            continue
        start, end = _num(raw.get("start")), _num(raw.get("end"))
        if start is None or end is None or end < start:
            continue
        out.append({"start": start, "end": end, "order": order, "raw": raw})
    out.sort(key=lambda s: (s["start"], s["end"], s["order"]))
    return out


def _valid_annotations(annotations: list[dict]) -> list[dict]:
    """Keep well-formed annotations in their (stored) order."""
    out = []
    for order, raw in enumerate(annotations or []):
        if not isinstance(raw, dict):
            continue
        start, end = _num(raw.get("start")), _num(raw.get("end"))
        if start is None or end is None or end < start:
            continue
        out.append({"id": str(raw.get("id") or ""),
                    "start": start, "end": end, "order": order, "raw": raw})
    return out


def _segment_copy(raw: dict) -> dict:
    """Include the full original segment record, untouched."""
    return dict(raw)


def _exclusion_reason(seg: dict, lo: float, hi: float) -> str:
    starts_before = seg["start"] < lo - EPS
    ends_after = seg["end"] > hi + EPS
    if starts_before and ends_after:
        return REASON_CROSSES_BOTH
    if starts_before:
        return REASON_CROSSES_START
    return REASON_CROSSES_END


def _intersects(start: float, end: float, lo: float, hi: float) -> bool:
    """Half-open intersection: touching endpoints do not intersect."""
    return start < hi - EPS and end > lo + EPS


def _contained(start: float, end: float, lo: float, hi: float) -> bool:
    """Fully inside the closed bounds (with EPS tolerance)."""
    return start >= lo - EPS and end <= hi + EPS


def build_excerpt(document: dict, raw_ranges: Any) -> dict:
    """Build the read-only excerpt-package preview / download payload.

    See the module docstring for the inclusion / clipping rules. The result
    contains the merged intervals, included segments, clipped annotations
    (each carrying ``sourceAnnotationId``) and the excluded cross-boundary
    segments; the original document is never modified.
    """
    ranges = normalize_ranges(raw_ranges)
    intervals = merge_inputs(ranges)
    segments = _valid_segments((document or {}).get("segments", []))
    annotations = _valid_annotations((document or {}).get("annotations", []))

    # segment key -> aggregated exclusion across all intervals (dedupe)
    excluded_map: dict[str, dict] = {}

    def note_exclusion(seg: dict, interval_index: int, reason: str) -> None:
        key = str(seg["raw"].get("id") or f"seg@{seg['order']}")
        entry = excluded_map.get(key)
        if entry is None:
            entry = {
                "key": key,
                "segment": _segment_copy(seg["raw"]),
                "intervalIndices": [],
                "reasons": [],
            }
            excluded_map[key] = entry
        if interval_index not in entry["intervalIndices"]:
            entry["intervalIndices"].append(interval_index)
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)

    total_segments = 0
    total_annotations = 0
    split_sources: set[str] = set()

    # First pass: which intervals does each annotation clip into? An
    # annotation spanning two disjoint intervals is split into one clip per
    # interval (id gets a -part suffix), and the original id is kept as the
    # source marker on every clip.
    ann_clips: dict[int, list[dict]] = {i: [] for i in range(len(intervals))}
    ann_parts: list[list[int]] = []   # interval indices per annotation
    for ann in annotations:
        hits = [iv["index"] for iv in intervals
                if _intersects(ann["start"], ann["end"],
                               iv["start"], iv["end"])]
        ann_parts.append(hits)
        if len(hits) >= 2:
            split_sources.add(ann["id"] or f"ann@{ann['order']}")

    for ann, hits in zip(annotations, ann_parts):
        source_id = ann["id"]
        base_id = source_id or f"ann-{ann['order'] + 1}"
        multi = len(hits) > 1
        for part, iv_index in enumerate(hits, start=1):
            iv = intervals[iv_index]
            lo, hi = iv["start"], iv["end"]
            clip_start = max(ann["start"], lo)
            clip_end = min(ann["end"], hi)
            if clip_end - clip_start <= EPS:
                continue
            clip = dict(ann["raw"])  # keep speaker/topic/note/rule provenance
            clip["id"] = f"exc-{base_id}-{part}" if multi else f"exc-{base_id}"
            clip["start"] = _r3(clip_start)
            clip["end"] = _r3(clip_end)
            clip["sourceAnnotationId"] = source_id
            if multi:
                clip["sourcePart"] = part
                clip["sourcePartCount"] = len(hits)
            ann_clips[iv_index].append(clip)
            total_annotations += 1

    for iv in intervals:
        lo, hi = iv["start"], iv["end"]
        included: list[dict] = []
        excluded: list[dict] = []
        for seg in segments:
            if _contained(seg["start"], seg["end"], lo, hi):
                included.append(_segment_copy(seg["raw"]))
            elif _intersects(seg["start"], seg["end"], lo, hi):
                reason = _exclusion_reason(seg, lo, hi)
                item = _segment_copy(seg["raw"])
                item["reason"] = reason
                excluded.append(item)
                note_exclusion(seg, iv["index"], reason)
        iv["segments"] = included
        iv["segmentCount"] = len(included)
        clips = ann_clips[iv["index"]]
        clips.sort(key=lambda a: (a["start"], a["end"]))
        iv["annotations"] = clips
        iv["annotationCount"] = len(clips)
        iv["excludedSegments"] = excluded
        iv["excludedSegmentCount"] = len(excluded)
        total_segments += len(included)

    excluded_segments = []
    for entry in excluded_map.values():
        item = entry["segment"]
        item["intervalIndices"] = sorted(entry["intervalIndices"])
        item["reason"] = (REASON_CROSSES_GAP if len(item["intervalIndices"]) > 1
                          else entry["reasons"][0])
        excluded_segments.append(item)
    excluded_segments.sort(key=lambda s: (
        _num(s.get("start")) if _num(s.get("start")) is not None else 0.0,
        _num(s.get("end")) if _num(s.get("end")) is not None else 0.0))

    return {
        "intervals": intervals,
        "excludedSegments": excluded_segments,
        "summary": {
            "inputRangeCount": len(ranges),
            "mergedRangeCount": len(intervals),
            "segmentCount": total_segments,
            "annotationCount": total_annotations,
            "excludedSegmentCount": len(excluded_segments),
            "splitAnnotationCount": len(split_sources),
        },
    }
