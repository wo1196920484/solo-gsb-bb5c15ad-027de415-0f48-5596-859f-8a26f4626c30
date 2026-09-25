"""Rule-based batch annotation engine.

A *rule* matches transcript segments by speaker, keyword / regular
expression and minimum duration.  Consecutive matching segments are merged
into one annotation interval carrying the rule's topic and a rendered note
template.  Annotations produced by a rule are tagged with
``source = "rule"`` plus ``ruleId`` so that re-running the same rule only
replaces its own output and never touches manual annotations.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

ANN_SOURCE = "rule"
DEFAULT_COLOR = "#5b8def"

MATCH_KEYWORD = "keyword"
MATCH_REGEX = "regex"
MATCH_NONE = "none"
MATCH_TYPES = (MATCH_KEYWORD, MATCH_REGEX, MATCH_NONE)


def compile_matcher(rule: dict):
    """Return a function(text) -> bool for the rule's keyword/regex.

    Raises ValueError (with a user-facing message) on invalid regexes.
    """
    match_type = rule.get("matchType") or MATCH_KEYWORD
    pattern = rule.get("pattern") or ""
    if match_type == MATCH_NONE or not pattern:
        return lambda _text: True
    if match_type == MATCH_KEYWORD:
        needle = pattern
        if not rule.get("caseSensitive"):
            needle = needle.lower()

        def match_keyword(text: str) -> bool:
            return needle in (text if rule.get("caseSensitive")
                              else text.lower())

        return match_keyword
    if match_type == MATCH_REGEX:
        flags = 0 if rule.get("caseSensitive") else re.IGNORECASE
        try:
            rx = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"正则表达式无效：{exc}") from exc
        return lambda text: rx.search(text) is not None
    raise ValueError(f"未知匹配方式：{match_type!r}")


def _round3(value: float) -> float:
    return round(float(value) + 0.0, 3)


def render_note(template: str, *, speakers: str, text: str, keyword: str,
                count: int) -> str:
    """Substitute the supported placeholders in a note/topic template."""
    if not template:
        return ""
    return (template
            .replace("{speaker}", speakers or "")
            .replace("{keyword}", keyword or "")
            .replace("{text}", text or "")
            .replace("{segments}", str(count)))


def _sorted_segments(segments: list[dict]) -> list[dict]:
    return sorted(segments, key=lambda s: (float(s.get("start", 0.0)),
                                           str(s.get("id", ""))))


def match_segments(rule: dict, segments: list[dict]) -> list[dict]:
    """All segments satisfying every condition of the rule."""
    wanted_speaker = (rule.get("speaker") or "").strip()
    min_duration = float(rule.get("minDuration") or 0.0)
    matches = compile_matcher(rule)
    hits = []
    for seg in _sorted_segments(segments):
        if wanted_speaker and (seg.get("speaker") or "") != wanted_speaker:
            continue
        try:
            duration = float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))
        except (TypeError, ValueError):
            continue
        if duration + 1e-9 < min_duration:
            continue
        if not matches(seg.get("text") or ""):
            continue
        hits.append(seg)
    return hits


def build_groups(all_segments: list[dict], matched: list[dict]) -> list[list[dict]]:
    """Consecutive (in transcript order) matched segments form one group."""
    matched_ids = {s.get("id") for s in matched}
    groups: list[list[dict]] = []
    current: list[dict] = []
    for seg in _sorted_segments(all_segments):
        if seg.get("id") in matched_ids:
            current.append(seg)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def generated_id(rule_id: str, index: int) -> str:
    suffix = rule_id.split("-", 1)[1] if rule_id.startswith("rule-") else rule_id
    return f"ann-rule-{suffix}-{index}"


def unique_id(rule_id: str, slot: int) -> str:
    suffix = rule_id.split("-", 1)[1] if rule_id.startswith("rule-") else rule_id
    return f"ann-rule-{suffix}-{slot}-{uuid.uuid4().hex[:8]}"


def build_annotations(rule: dict, rule_id: str, segments: list[dict]) -> list[dict]:
    """Annotations a rule would generate, one per consecutive hit group.

    Each item carries the deterministic ``ruleSlot`` (group index) which is
    the stable identity used when re-running the rule; the annotation ``id``
    only coincides with the slot's generated id while nobody manually
    detached / deleted it.
    """
    matched = match_segments(rule, segments)
    groups = build_groups(segments, matched)
    result = []
    for i, group in enumerate(groups):
        start = _round3(float(group[0].get("start", 0.0)))
        end = _round3(float(group[-1].get("end", 0.0)))
        speakers = []
        for s in group:
            sp = s.get("speaker") or ""
            if sp and sp not in speakers:
                speakers.append(sp)
        text = " ".join((s.get("text") or "").strip() for s in group).strip()
        tpl_vars = {
            "speakers": speakers[0] if len(speakers) == 1 else "",
            "text": text,
            "keyword": rule.get("pattern") or "",
            "count": len(group),
        }
        ann: dict[str, Any] = {
            "id": generated_id(rule_id, i),
            "ruleSlot": i,
            "start": start,
            "end": end,
            "speaker": speakers[0] if len(speakers) == 1 else "",
            "topic": render_note(rule.get("topic") or "", **tpl_vars),
            "note": render_note(rule.get("noteTemplate") or "", **tpl_vars),
            "color": rule.get("color") or DEFAULT_COLOR,
            "source": ANN_SOURCE,
            "ruleId": rule_id,
            "ruleName": rule.get("name") or "",
        }
        result.append(ann)
    return result


def is_owned(ann: dict, rule_id: str) -> bool:
    return ann.get("source") == ANN_SOURCE and ann.get("ruleId") == rule_id


def plan_apply(rule: dict, rule_id: str, document: dict,
               tombstone_slots: set[int] | None = None) -> dict:
    """Plan every change an apply would make.

    Ownership of a rule's output is the ``(ruleId, ruleSlot)`` pair.
    Existing annotations owned by the rule are replaced in place; manual
    annotations are never touched.  If a slot's previous output was
    detached by a manual edit its id stays occupied, so the replacement gets
    a fresh unique id.  Slots listed in ``tombstone_slots`` were manually
    deleted and stay deleted.

    Returns proposed annotations (final ids/metadata), hit groups and the
    counters used by both the preview API and the transactional writer.
    """
    tombstone_slots = tombstone_slots or set()
    segments = document.get("segments") or []
    annotations = document.get("annotations") or []
    proposed_all = build_annotations(rule, rule_id, segments)
    groups = build_groups(segments, match_segments(rule, segments))

    existing_ids = {a.get("id") for a in annotations}
    owned_by_slot: dict[int, dict] = {}
    for ann in annotations:
        if is_owned(ann, rule_id):
            slot = ann.get("ruleSlot")
            if isinstance(slot, int):
                owned_by_slot[slot] = ann

    hits, to_insert = [], []
    created = updated = kept_manual = suppressed = 0
    for group, base in zip(groups, proposed_all):
        slot = base["ruleSlot"]
        if slot in tombstone_slots:
            suppressed += 1
            continue
        ann = dict(base)
        current = owned_by_slot.get(slot)
        if current is not None:
            ann["id"] = current["id"]  # update the existing annotation
            updated += 1
            action = "update"
        elif ann["id"] in existing_ids:
            # a manual (detached) annotation occupies the deterministic id
            ann["id"] = unique_id(rule_id, slot)
            created += 1
            kept_manual += 1
            action = "createManualKept"
        else:
            created += 1
            action = "create"
        existing_ids.add(ann["id"])
        to_insert.append(ann)
        hits.append({
            "index": len(hits),
            "action": action,
            "segmentIds": [s.get("id") for s in group],
            "segments": [
                {"id": s.get("id"), "start": s.get("start"), "end": s.get("end"),
                 "speaker": s.get("speaker", ""), "text": s.get("text", "")}
                for s in group
            ],
            "annotation": ann,
        })

    active_slots = {a["ruleSlot"] for a in to_insert}
    removed = sum(1 for slot, ann in owned_by_slot.items()
                  if slot not in active_slots)
    return {
        "hits": hits,
        "proposed": to_insert,
        "owned": list(owned_by_slot.values()),
        "stats": {
            "matchedSegments": sum(len(g) for g in groups),
            "groups": len(groups),
            "willCreate": created,
            "willUpdate": updated,
            "willRemove": removed,
            "keptManual": kept_manual,
            "suppressed": suppressed,
        },
    }


def evaluate_rule(rule: dict, rule_id: str, document: dict,
                  tombstone_slots: set[int] | None = None) -> dict:
    """Preview a rule against a document (see :func:`plan_apply`)."""
    plan = plan_apply(rule, rule_id, document, tombstone_slots)
    return {
        "ruleId": rule_id,
        "matchedSegments": plan["stats"]["matchedSegments"],
        "groups": plan["stats"]["groups"],
        "hits": plan["hits"],
        "proposed": plan["proposed"],
        "stats": plan["stats"],
    }
