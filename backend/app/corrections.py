"""转写错词批量校正引擎（batch correction of transcription error words）。

用户配置多组「错词 → 正词」，基于**已保存**的项目转写逐片段预览命中与
校正后文本，确认后在**一次事务**中改写命中片段的 ``text`` 字段——片段
ID、起止时间、说话人与全部校注原样保留。

校正规则：

* **逐片段独立**：每个片段只扫描自己的原文，错词不会跨片段匹配；
* **单遍扫描、替换不回扫**：从左到右扫描原文一次，命中即输出正词并
  跳过错词长度，**替换出的文字不继续参与匹配**（正词里即使含有别的
  错词也不会被二次替换）；
* **同一位置优先最长错词**：多个错词能在同一位置命中时，最长的错词
  胜出（如同时配置「的」与「得的」，原文「得的」整体替换，而不是先
  把「的」替换掉）。

词表校验（预览与确认执行同一套）：错词为空的行、重复配置的错词都会
被拒绝（ValueError），提示用户先修正词表；正词允许为空，表示删除该
错词。

确认时的并发保护：预览返回 ``transcript_version``（已保存转写片段的
SHA-256 指纹），确认请求必须带回该版本；若期间已保存的转写被编辑或
重新导入，指纹不一致，写入整体拒绝并要求重新预览。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize_pairs(pairs: Any) -> list[dict]:
    """Validate and normalize the wrong→right word pairs.

    Returns ``[{"from": wrong, "to": right}, ...]`` with surrounding
    whitespace stripped.  Raises ValueError when any row has an empty
    wrong word or when a wrong word is configured more than once.  An
    empty right word is allowed and means "delete the wrong word".
    """
    if not isinstance(pairs, list):
        raise ValueError("校正词表格式不正确")
    out: list[dict] = []
    seen: set[str] = set()
    for raw in pairs:
        record = raw if isinstance(raw, dict) else {}
        wrong = record.get("from")
        right = record.get("to")
        wrong = wrong.strip() if isinstance(wrong, str) else ""
        right = right.strip() if isinstance(right, str) else ""
        if not wrong:
            raise ValueError("存在未填写错词的校正行，请补全或删除该行")
        if wrong in seen:
            raise ValueError(
                f"错词「{wrong}」配置了多组校正，请去重后再提交")
        seen.add(wrong)
        out.append({"from": wrong, "to": right})
    return out


def correct_text(text: str, pairs: list[dict]) -> tuple[str, list[dict]]:
    """Correct one segment's text in a single left-to-right pass.

    At every position the **longest** wrong word that matches wins; the
    replacement is emitted verbatim and never rescanned, so text produced
    by a replacement cannot trigger further replacements.  Returns the
    corrected text and the hit list — each hit is
    ``{"from", "to", "start", "end"}`` with half-open character offsets
    into the *original* text.
    """
    if not text or not pairs:
        return text, []
    by_first: dict[str, list[dict]] = {}
    for pair in pairs:
        by_first.setdefault(pair["from"][0], []).append(pair)
    for candidates in by_first.values():
        # longest wrong word first -> longest match wins at each position
        candidates.sort(key=lambda p: len(p["from"]), reverse=True)

    hits: list[dict] = []
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        match = None
        for pair in by_first.get(text[i], ()):
            wrong = pair["from"]
            if text.startswith(wrong, i):
                match = pair
                break
        if match is None:
            out.append(text[i])
            i += 1
        else:
            wrong = match["from"]
            hits.append({"from": wrong, "to": match["to"],
                         "start": i, "end": i + len(wrong)})
            out.append(match["to"])
            i += len(wrong)
    return "".join(out), hits


def transcript_version(segments: Any) -> str:
    """Stable fingerprint of the saved transcript segments.

    Covers every field of every segment (id, times, speaker, text, …), so
    any edit to the saved transcript — or a re-import — changes the
    version and stale previews are rejected at confirm time.
    """
    canonical = json.dumps(segments or [], ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _segment_text(record: Any) -> str:
    text = record.get("text") if isinstance(record, dict) else None
    return text if isinstance(text, str) else ""


def preview_corrections(document: dict, pairs: Any) -> dict:
    """Build the read-only preview: per-segment hits and corrected text.

    Only segments with at least one hit are listed.  The source document
    is never modified.
    """
    norm = normalize_pairs(pairs)
    segments = (document or {}).get("segments", []) or []
    word_counts = {p["from"]: 0 for p in norm}
    results: list[dict] = []
    total_hits = 0
    changed_segments = 0
    for raw in segments:
        record = raw if isinstance(raw, dict) else {}
        text = _segment_text(record)
        corrected, hits = correct_text(text, norm)
        if not hits:
            continue
        for hit in hits:
            word_counts[hit["from"]] += 1
        total_hits += len(hits)
        changed = corrected != text
        if changed:
            changed_segments += 1
        speaker = record.get("speaker")
        results.append({
            "id": record.get("id"),
            "start": record.get("start"),
            "end": record.get("end"),
            "speaker": speaker if isinstance(speaker, str) else "",
            "originalText": text,
            "correctedText": corrected,
            "changed": changed,
            "hits": hits,
            "hitCount": len(hits),
        })
    return {
        "transcriptVersion": transcript_version(segments),
        "pairs": norm,
        "words": [{"from": p["from"], "to": p["to"],
                   "count": word_counts[p["from"]]} for p in norm],
        "segments": results,
        "summary": {
            "segmentCount": len(segments),
            "pairCount": len(norm),
            "hitSegmentCount": len(results),
            "changedSegmentCount": changed_segments,
            "hitCount": total_hits,
        },
    }


def corrected_texts(segments: list, pairs: list[dict]) -> tuple[dict, int]:
    """``{segment index: corrected text}`` for segments that actually change.

    ``pairs`` must already be normalized (see :func:`normalize_pairs`).
    Also returns the number of replacements inside those changed segments.
    """
    new_texts: dict[int, str] = {}
    replacements = 0
    for index, raw in enumerate(segments or []):
        text = _segment_text(raw)
        corrected, hits = correct_text(text, pairs)
        if hits and corrected != text:
            new_texts[index] = corrected
            replacements += len(hits)
    return new_texts, replacements
