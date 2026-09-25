"""公开版转写引擎（public transcript / redaction）。

基于**已保存**的项目文档生成一份可对外公开的转写，规则有两类：

1. **敏感词遮盖**：输入多条敏感词，匹配时**忽略空白**（敏感词与片段文本中的
   所有 Unicode 空白字符都不参与匹配），命中可**跨越多个时间相接且说话人
   相同（非空）的片段**；片段之间有空隙、重叠或说话人切换时匹配链断开
   （与引语检索共用同一套链接规则，绝不会把跨说话人的文字连成一次命中）。
   每次命中的**非空白字符**按原字符位置替换为方块 ``■``，原文空白原样
   保留；不同敏感词（或同一敏感词的不同出现位置）的命中重叠时，同一字符
   **只遮盖一次**（遮盖集合为各命中字符位置的并集）。
2. **说话人匿名**：选中的说话人替换为一致的匿名编号（``说话人 1``、
   ``说话人 2``……），编号按其在已保存转写片段中首次出现的顺序分配，
   选中但未出现的名称按输入顺序递补，因此预览与下载始终使用同一份编号；
   未选中的说话人名称原样保留。

输出**只含转写片段**（不含校注）：片段 ID、起止时间与分段划分全部保留，
整个过程只读项目文档，不写回任何数据。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

EPS = 1e-6
MASK_CHAR = "■"


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _normalize_words(words: Any, case_sensitive: bool) -> list[str]:
    """Strip all whitespace from each word, drop empties, dedupe (keep order)."""
    if not isinstance(words, list):
        raise ValueError("敏感词列表格式不正确")
    seen: set[str] = set()
    out: list[str] = []
    for raw in words:
        if not isinstance(raw, str):
            raise ValueError("敏感词必须是字符串")
        word = "".join(ch for ch in raw if not ch.isspace())
        if not word:
            continue
        key = word if case_sensitive else word.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
    return out


def _normalize_speakers(raw_speakers: Any) -> list[str]:
    """Trim speaker names, drop blanks, dedupe (keep input order)."""
    if not isinstance(raw_speakers, list):
        raise ValueError("需匿名的说话人列表格式不正确")
    seen: set[str] = set()
    out: list[str] = []
    for raw in raw_speakers:
        if not isinstance(raw, str):
            raise ValueError("说话人名称必须是字符串")
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _prepare_segments(segments: Any) -> list[dict]:
    """Normalize every segment row, keeping document order and the raw record."""
    out = []
    for order, raw in enumerate(segments or []):
        record = raw if isinstance(raw, dict) else {}
        start, end = _num(record.get("start")), _num(record.get("end"))
        speaker = record.get("speaker")
        text = record.get("text")
        out.append({
            "order": order,
            "raw": record,
            "start": start,
            "end": end,
            "speaker": speaker if isinstance(speaker, str) else "",
            "text": text if isinstance(text, str) else "",
            "valid": start is not None and end is not None and end >= start,
        })
    return out


def _build_chains(prepared: list[dict]) -> list[list[dict]]:
    """Group valid segments into maximal adjacent same-speaker runs.

    Two consecutive (in time order) segments chain only when they touch in
    time (``prev.end == next.start``) and carry the same non-empty speaker;
    gaps, overlaps, speaker switches and missing speakers break the chain.
    Rows with invalid times cannot be chained, so each becomes a singleton
    chain — sensitive words fully inside them are still masked.
    """
    valid = sorted((p for p in prepared if p["valid"]),
                   key=lambda s: (s["start"], s["end"], s["order"]))
    chains: list[list[dict]] = []
    for seg in valid:
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
    chains.extend([p] for p in prepared if not p["valid"])
    return chains


def _chain_masks(chain: list[dict], needles: list[str],
                 case_sensitive: bool) -> tuple[dict[int, set[int]], list[int]]:
    """Mask positions for one chain.

    Returns ``{chain-local segment index: set(char indices)}`` — the union of
    every hit's non-whitespace characters, so overlapping hits mask a
    character only once — plus the per-word occurrence counts in this chain.
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
    hay_cmp = hay if case_sensitive else hay.lower()

    masks: dict[int, set[int]] = {}
    counts = [0] * len(needles)
    for wi, needle in enumerate(needles):
        probe = needle if case_sensitive else needle.lower()
        cursor = 0
        while True:
            at = hay_cmp.find(probe, cursor)
            if at < 0:
                break
            cursor = at + len(probe)  # non-overlapping occurrences per word
            counts[wi] += 1
            for pos in range(at, cursor):
                si, ci = positions[pos]
                masks.setdefault(si, set()).add(ci)
    return masks, counts


def _mask_text(text: str, chars: set[int]) -> str:
    """Replace the given original character positions with blocks.

    Only non-whitespace characters are ever substituted; the original
    whitespace (and therefore the text's length and layout) is preserved.
    """
    return "".join(
        MASK_CHAR if i in chars and not ch.isspace() else ch
        for i, ch in enumerate(text)
    )


def _runs(chars: set[int]) -> list[list[int]]:
    """Collapse character indices into sorted half-open runs (preview aid)."""
    out: list[list[int]] = []
    for ci in sorted(chars):
        if out and ci == out[-1][1]:
            out[-1][1] += 1
        else:
            out.append([ci, ci + 1])
    return out


def _alias_map(prepared: list[dict], selected: list[str]) -> dict[str, str]:
    """Assign each selected speaker a stable anonymous label.

    Numbering follows first appearance in the saved transcript (document
    order); selected names that never appear receive the following numbers
    in input order, so the preview and the download always agree.
    """
    wanted = set(selected)
    aliases: dict[str, str] = {}
    for seg in prepared:
        name = seg["speaker"].strip()
        if name and name in wanted and name not in aliases:
            aliases[name] = f"说话人 {len(aliases) + 1}"
    for name in selected:
        if name not in aliases:
            aliases[name] = f"说话人 {len(aliases) + 1}"
    return aliases


def build_public_transcript(document: dict, sensitive_words: Any,
                            anonymize_speakers: Any,
                            case_sensitive: bool = False) -> dict:
    """Build the read-only public-transcript preview / download payload.

    See the module docstring for the masking / anonymizing rules. The source
    document is never modified.
    """
    words = _normalize_words(sensitive_words, case_sensitive)
    selected = _normalize_speakers(anonymize_speakers)
    prepared = _prepare_segments((document or {}).get("segments", []))
    aliases = _alias_map(prepared, selected)

    # mask positions per segment (keyed by document order), unioned across
    # every chain and every sensitive word
    seg_masks: dict[int, set[int]] = {}
    word_counts = [0] * len(words)
    for chain in _build_chains(prepared):
        masks, counts = _chain_masks(chain, words, case_sensitive)
        for wi, n in enumerate(counts):
            word_counts[wi] += n
        for si, chars in masks.items():
            seg_masks.setdefault(chain[si]["order"], set()).update(chars)

    speaker_segment_counts = {name: 0 for name in selected}
    segments: list[dict] = []
    masked_char_count = 0
    masked_segment_count = 0
    anonymized_segment_count = 0
    for seg in prepared:
        chars = seg_masks.get(seg["order"], set())
        out = dict(seg["raw"])  # segment id, start/end and any extra fields
        out["text"] = _mask_text(seg["text"], chars) if chars else seg["text"]
        speaker_key = seg["speaker"].strip()
        alias = aliases.get(speaker_key) if speaker_key else None
        out["speaker"] = alias if alias else seg["speaker"]
        # preview aids (stripped from the download body)
        out["masked"] = bool(chars)
        out["maskedChars"] = len(chars)
        out["maskedRanges"] = _runs(chars)
        out["anonymized"] = bool(alias)
        segments.append(out)

        if speaker_key in speaker_segment_counts:
            speaker_segment_counts[speaker_key] += 1
        if chars:
            masked_segment_count += 1
            masked_char_count += len(chars)
        if alias:
            anonymized_segment_count += 1

    speakers_out = [
        {
            "original": name,
            "alias": aliases[name],
            "segmentCount": speaker_segment_counts[name],
            "appears": speaker_segment_counts[name] > 0,
        }
        for name in selected
    ]

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "caseSensitive": bool(case_sensitive),
        "sensitiveWords": words,
        "words": [{"word": w, "count": c} for w, c in zip(words, word_counts)],
        "speakers": speakers_out,
        "segments": segments,
        "summary": {
            "segmentCount": len(segments),
            "hitCount": sum(word_counts),
            "maskedSegmentCount": masked_segment_count,
            "maskedCharCount": masked_char_count,
            "anonymizedSpeakerCount":
                sum(1 for sp in speakers_out if sp["appears"]),
            "anonymizedSegmentCount": anonymized_segment_count,
        },
    }


def public_document(result: dict) -> dict:
    """Reduce a preview payload to the download body: segments only.

    The public file carries no annotations, no original names of anonymized
    speakers and no sensitive-word list — each segment keeps exactly its id,
    start/end times, (possibly anonymized) speaker and masked text.
    """
    return {
        "kind": "public-transcript",
        "projectId": result.get("projectId"),
        "projectName": result.get("projectName"),
        "generatedAt": result.get("generatedAt"),
        "segmentCount": result["summary"]["segmentCount"],
        "segments": [
            {
                "id": seg.get("id"),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "speaker": seg.get("speaker", ""),
                "text": seg.get("text", ""),
            }
            for seg in result["segments"]
        ],
    }
