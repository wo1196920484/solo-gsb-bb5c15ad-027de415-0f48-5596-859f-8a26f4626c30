"""Parse imported transcripts into the canonical document shape.

Accepted inputs:
1. canonical document JSON: {"segments": [...], "annotations": [...]}
2. JSON array of segment objects (start/end, speaker?, text)
3. plain text, one segment per line:
     [00:00:01] Speaker A: 你好
     [00:01.5] 大家好
     00:00:03,500 --> 00:00:05,000  Speaker B: 文字
"""
from __future__ import annotations

import json
import re

from .db import new_id

# [hh:mm:ss(.|,)mmm] or [mm:ss(.|,)mmm]
_TS = r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?"
LINE_RE = re.compile(
    rf"^\s*\[?(?P<start>{_TS})\]?"
    rf"(?:\s*--?>\s*\[?(?P<end>{_TS})\]?)?"
    r"\s*(?P<rest>.*)$"
)
SPEAKER_RE = re.compile(r"^(?P<speaker>[^:：]{1,40})[:：]\s*(?P<text>.+)$")
_NUM_RE = re.compile(r"^\d+$")


def _ts_to_seconds(token: str) -> float:
    token = token.replace(",", ".")
    parts = token.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    h, m, s = parts
    return h * 3600 + m * 60 + s


def parse_document(raw: str | dict | list) -> dict:
    """Parse uploaded content into {"segments": [...], "annotations": [...]}."""
    if isinstance(raw, (dict, list)):
        data = raw
    else:
        text = raw.strip()
        if not text:
            raise ValueError("导入内容为空")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return _parse_text(text)

    if isinstance(data, dict):
        segments = data.get("segments")
        if not isinstance(segments, list):
            raise ValueError("JSON 文档必须包含 segments 数组")
        doc = {"segments": [_clean_segment(s) for s in segments],
               "annotations": []}
        annotations = data.get("annotations") or []
        if not isinstance(annotations, list):
            raise ValueError("annotations 必须是数组")
        doc["annotations"] = [_clean_annotation(a) for a in annotations]
        return doc

    if isinstance(data, list):
        segments, annotations = [], []
        for raw in data:
            seg = _clean_segment(raw)
            if raw.get("topic") or raw.get("note"):
                annotations.append({
                    "id": new_id("ann"),
                    "start": seg["start"], "end": seg["end"],
                    "speaker": seg["speaker"],
                    "topic": str(raw.get("topic", "") or ""),
                    "note": str(raw.get("note", "") or ""),
                })
            segments.append(seg)
        return {"segments": segments, "annotations": annotations}

    raise ValueError("无法识别的 JSON 结构")


def _clean_segment(raw: dict, fallback_id: str | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"片段必须是对象，收到 {type(raw).__name__}")
    start = _num(raw.get("start", raw.get("startTime", raw.get("begin"))), "start")
    end = _num(raw.get("end", raw.get("endTime", raw.get("stop"))), "end",
               default=start)
    if end < start:
        raise ValueError(f"片段 {start}-{end} 的结束时间早于开始时间")
    text = raw.get("text", raw.get("content", ""))
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)
    speaker = raw.get("speaker", raw.get("speakerName", "")) or ""
    sid = raw.get("id") or fallback_id or new_id("seg")
    if not isinstance(sid, str) or not sid:
        sid = new_id("seg")
    return {"id": sid, "start": start, "end": end,
            "speaker": str(speaker), "text": text}


def _clean_annotation(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("标注必须是对象")
    start = _num(raw.get("start", raw.get("startTime")), "start")
    end = _num(raw.get("end", raw.get("endTime")), "end", default=start)
    if end < start:
        raise ValueError(f"标注 {start}-{end} 的结束时间早于开始时间")
    return {
        "id": str(raw.get("id") or new_id("ann")),
        "start": start,
        "end": end,
        "speaker": str(raw.get("speaker", "") or ""),
        "topic": str(raw.get("topic", "") or ""),
        "note": str(raw.get("note", "") or ""),
    }


def _num(value, field: str, default: float | None = None) -> float:
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"缺少 {field} 时间")
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if ":" in value:
            return _ts_to_seconds(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 不是有效的时间: {value!r}")


def _parse_text(text: str) -> dict:
    segments, annotations = [], []
    pending_start: float | None = None
    pending: dict | None = None
    pending_locked = False

    def flush(next_start: float | None = None):
        nonlocal pending, pending_start, pending_locked
        if pending is not None:
            if not pending_locked:
                if next_start is not None and next_start > pending["start"]:
                    pending["end"] = next_start
                else:
                    pending["end"] = round(pending["start"] + 1.0, 3)
            segments.append(pending)
        pending, pending_start, pending_locked = None, None, False

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        start = _ts_to_seconds(m.group("start"))
        end = _ts_to_seconds(m.group("end")) if m.group("end") else None
        rest = m.group("rest").strip()

        speaker, body = "", rest
        sm = SPEAKER_RE.match(rest)
        if sm and not _NUM_RE.match(rest):
            speaker, body = sm.group("speaker").strip(), sm.group("text").strip()

        flush(start if pending_start is not None and start > pending_start else None)
        pending = {
            "id": new_id("seg"),
            "start": start,
            "end": start + 1.0,
            "speaker": speaker,
            "text": body,
        }
        pending_locked = False
        if end is not None:
            pending["end"] = end
            pending_locked = True
        pending_start = start
    flush()

    if not segments:
        raise ValueError("未能从文本中解析出任何带时间戳的片段")
    return {"segments": segments, "annotations": annotations}
