"""引语检索引擎（quote search）。

在项目的转写片段中检索一段引语，命中可以**跨越多个片段**，但严格遵守两条
边界规则：

1. 只拼接**时间相接**（上一片段的 end 与下一片段的 start 相等，允许微小
   浮点误差）且在时间顺序上相邻的片段；片段之间存在空隙、重叠，或中间夹着
   另一个片段时，链在该处断开；
2. 一条链上所有片段的**说话人必须相同且非空**，说话人切换（或说话人
   缺失）时链断开——因此绝不会把跨说话人的文字误判为同一句引语。

检索时**忽略空白**：查询串与片段文本中的所有 Unicode 空白字符都不参与
匹配（``"后来 2003 年"`` 可以命中查询 ``"后来2003年"``）。与此同时，保留
每个参与匹配的字符到其原片段、原字符下标的映射，结果按片段给出逐字的
命中区间（``ranges``，半开区间），前端据此在原文中逐字标出命中字符。
"""
from __future__ import annotations

from typing import Any

EPS = 1e-6


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _valid_segments(segments: list[dict]) -> list[dict]:
    """Keep well-formed segments, sorted by time (start, end)."""
    out = []
    for order, raw in enumerate(segments or []):
        if not isinstance(raw, dict):
            continue
        start, end = _num(raw.get("start")), _num(raw.get("end"))
        if start is None or end is None or end < start:
            continue
        out.append({
            "id": str(raw.get("id") or ""),
            "start": start,
            "end": end,
            "speaker": str(raw.get("speaker") or ""),
            "text": raw.get("text") if isinstance(raw.get("text"), str) else "",
            "_order": order,
        })
    out.sort(key=lambda s: (s["start"], s["end"], s["_order"]))
    return out


def _build_chains(segments: list[dict]) -> list[list[dict]]:
    """Group segments into maximal adjacent same-speaker runs.

    Two consecutive (in time order) segments belong to the same chain only
    when they touch in time (``prev.end == next.start``) and carry the same
    non-empty speaker. Gaps, overlaps, speaker switches and missing speakers
    break a chain.
    """
    chains: list[list[dict]] = []
    for seg in segments:
        if chains:
            prev = chains[-1][-1]
            connected = (
                bool(seg["speaker"])
                and seg["speaker"] == prev["speaker"]
                and abs(seg["start"] - prev["end"]) <= EPS
            )
            if connected:
                chains[-1].append(seg)
                continue
        chains.append([seg])
    return chains


def _normalize(query: str, case_sensitive: bool) -> str:
    """Drop all whitespace; optionally fold case for matching."""
    text = "".join(ch for ch in query if not ch.isspace())
    return text if case_sensitive else text.lower()


def _chain_hits(chain: list[dict], needle: str,
                case_sensitive: bool) -> list[dict]:
    """Find all non-overlapping occurrences of ``needle`` in one chain.

    Builds the chain's whitespace-stripped text together with a per-character
    map back to (segment index within chain, char index within that segment).
    """
    # (chain-local segment index, char index in the original segment text)
    positions: list[tuple[int, int]] = []
    parts: list[str] = []
    for si, seg in enumerate(chain):
        for ci, ch in enumerate(seg["text"]):
            if ch.isspace():
                continue
            positions.append((si, ci))
            parts.append(ch)
    hay = "".join(parts)
    if not case_sensitive:
        hay = hay.lower()
    if not needle or not hay:
        return []

    hits: list[dict] = []
    cursor = 0
    while True:
        at = hay.find(needle, cursor)
        if at < 0:
            break
        cursor = at + len(needle)

        # Collect the matched characters' original locations, grouped per
        # segment into half-open character ranges.
        per_seg: dict[int, list[int]] = {}
        matched_chars: list[str] = []
        for pos in range(at, cursor):
            si, ci = positions[pos]
            per_seg.setdefault(si, []).append(ci)
            matched_chars.append(chain[si]["text"][ci])

        covered = []
        for si in sorted(per_seg):
            offsets = sorted(per_seg[si])
            seg = chain[si]
            runs: list[list[int]] = []
            run_start = prev = offsets[0]
            for ci in offsets[1:]:
                if ci == prev + 1:
                    prev = ci
                    continue
                runs.append([run_start, prev + 1])
                run_start = prev = ci
            runs.append([run_start, prev + 1])
            covered.append({
                "id": seg["id"],
                "start": seg["start"],
                "end": seg["end"],
                "speaker": seg["speaker"],
                "text": seg["text"],
                "matchStart": offsets[0],
                "matchEnd": offsets[-1] + 1,
                "ranges": runs,
            })

        seg_indexes = sorted(per_seg)
        first_seg = chain[seg_indexes[0]]
        last_seg = chain[seg_indexes[-1]]
        hits.append({
            "start": first_seg["start"],
            "end": last_seg["end"],
            "speaker": first_seg["speaker"],
            "matchedText": "".join(matched_chars),
            "segmentCount": len(covered),
            "segments": covered,
        })
    return hits


def search_quotes(document: dict, query: str, speaker: str | None = None,
                  case_sensitive: bool = False) -> dict:
    """Search a quote phrase across the document's segments.

    Returns ``{"query", "speaker", "count", "matches"}``. A query that is
    empty (or whitespace only) performs no search and raises ValueError.
    """
    needle = _normalize(query or "", case_sensitive)
    if not needle:
        raise ValueError("查询内容为空")

    speaker_filter = (speaker or "").strip()
    segments = _valid_segments((document or {}).get("segments", []))

    matches: list[dict] = []
    for chain in _build_chains(segments):
        chain_speaker = chain[0]["speaker"]
        if speaker_filter and chain_speaker != speaker_filter:
            continue
        matches.extend(_chain_hits(chain, needle, case_sensitive))

    matches.sort(key=lambda m: (m["start"], m["end"], m["speaker"]))
    return {
        "query": query,
        "speaker": speaker_filter or None,
        "count": len(matches),
        "matches": matches,
    }
