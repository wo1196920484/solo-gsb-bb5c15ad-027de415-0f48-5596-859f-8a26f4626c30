"""校注质检引擎（annotation quality check）。

在给定时间区间（窗口）上建立扫描线（sweep line）：把校注与转写片段的
端点作为事件，逐段统计每个小区间上同时活动的校注 / 片段，检测三类问题：

1. ``out_of_bounds`` 校注越界——校注区间落在转写片段覆盖范围之外的部分
   （含越过整条转写的首尾边界，以及覆盖内部空隙）；
2. ``overlap`` 校注之间的时间重叠（端点相接不算重叠）；
3. ``speaker_mismatch`` 校注说话人与其覆盖的转写片段说话人不一致
   （校注完全没有填写说话人时另记 ``speaker_missing`` 信息级问题）。

每条问题带稳定 ``issueId``（只取决于校注 id，与运行次数 / 时间窗口无关）、
``severity``、定位用的 ``start/end`` 与高亮 id，以及证据片段。越界问题在
可裁剪时给出 ``fix = {start, end}`` 建议，前端据此一键裁剪后保存。
"""
from __future__ import annotations

from typing import Any

EPS = 1e-9

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"
SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

TYPE_OUT_OF_BOUNDS = "out_of_bounds"
TYPE_OVERLAP = "overlap"
TYPE_SPEAKER_MISMATCH = "speaker_mismatch"
TYPE_SPEAKER_MISSING = "speaker_missing"


def _r3(value: float) -> float:
    return round(float(value) + 0.0, 3)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _valid_items(items: list[dict]) -> list[dict]:
    """Keep items with numeric start/end where end >= start."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        start, end = _num(it.get("start")), _num(it.get("end"))
        if start is None or end is None or end < start:
            continue
        out.append({"id": str(it.get("id") or ""), "start": start,
                    "end": end, "raw": it})
    return out


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping / touching (closed-interval) ranges."""
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [list(ranges[0])]
    for start, end in ranges[1:]:
        if start <= merged[-1][1] + EPS:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _subtract_ranges(interval: tuple[float, float],
                     covered: list[tuple[float, float]]
                     ) -> list[tuple[float, float]]:
    """Parts of ``interval`` not covered by the (merged) ``covered`` ranges."""
    start, end = interval
    pieces: list[tuple[float, float]] = []
    cursor = start
    for cs, ce in covered:
        if ce <= cursor + EPS:
            continue
        if cs >= end - EPS:
            break
        if cs > cursor + EPS:
            pieces.append((cursor, min(cs, end)))
        cursor = max(cursor, ce)
        if cursor >= end - EPS:
            break
    if cursor < end - EPS:
        pieces.append((cursor, end))
    return [(s, e) for s, e in pieces if e - s > EPS]


def _intersect_ranges(interval: tuple[float, float],
                      ranges: list[tuple[float, float]]
                      ) -> list[tuple[float, float]]:
    start, end = interval
    out = []
    for cs, ce in ranges:
        s, e = max(start, cs), min(end, ce)
        if e - s > EPS:
            out.append((s, e))
    return _merge_ranges(out)


def _topic(ann: dict) -> str:
    return (ann.get("topic") or "").strip() or "（未命名主题）"


def _slim_ann(ann: dict) -> dict:
    return {
        "id": ann.get("id"),
        "start": _r3(_num(ann.get("start")) or 0.0),
        "end": _r3(_num(ann.get("end")) or 0.0),
        "speaker": ann.get("speaker") or "",
        "topic": ann.get("topic") or "",
    }


def _slim_seg(seg: dict, covered: float | None = None) -> dict:
    item = {
        "id": seg.get("id"),
        "start": _r3(_num(seg.get("start")) or 0.0),
        "end": _r3(_num(seg.get("end")) or 0.0),
        "speaker": seg.get("speaker") or "",
        "text": (seg.get("text") or "").strip(),
    }
    if covered is not None:
        item["coveredSeconds"] = _r3(covered)
    return item


def _resolve_window(document: dict,
                    start: float | None,
                    end: float | None) -> tuple[float, float, bool]:
    explicit = start is not None or end is not None
    segs = _valid_items(document.get("segments", []))
    anns = _valid_items(document.get("annotations", []))
    bounds = [(it["start"], it["end"]) for it in segs + anns
              if it["end"] - it["start"] > EPS]
    if not bounds:
        lo, hi = 0.0, 0.0
    else:
        lo = min(s for s, _ in bounds)
        hi = max(e for _, e in bounds)
    win_start = lo if start is None else max(0.0, start)
    win_end = hi if end is None else max(win_start, end)
    return win_start, win_end, explicit


def _sweep(anns: list[dict], segs: list[dict],
           window: tuple[float, float]):
    """Cut the window at every interval endpoint and walk the elementary spans.

    Returns (spans, touch, pair_overlap):
      * ``spans``            — number of elementary spans scanned;
      * ``touch[(ai,sj)]``   — seconds annotation ai overlaps segment sj;
      * ``pair_overlap[(a,b)]`` — elementary overlap ranges (a index < b).
    Indexes are positions inside the *window-clipped* item lists.
    """
    win_lo, win_hi = window

    def clipped(it):
        s, e = max(it["start"], win_lo), min(it["end"], win_hi)
        return (s, e) if e - s > EPS else None

    c_anns = [(it, clipped(it)) for it in anns]
    c_segs = [(it, clipped(it)) for it in segs]
    c_anns = [(it, c) for it, c in c_anns if c]
    c_segs = [(it, c) for it, c in c_segs if c]

    bounds = {win_lo, win_hi}
    for _, (s, e) in c_anns + c_segs:
        bounds.add(s)
        bounds.add(e)
    xs = sorted(x for x in bounds if win_lo - EPS <= x <= win_hi + EPS)

    touch: dict[tuple[int, int], float] = {}
    pair_overlap: dict[tuple[int, int], list[tuple[float, float]]] = {}
    spans = 0
    for x1, x2 in zip(xs, xs[1:]):
        if x2 - x1 <= EPS:
            continue
        spans += 1
        active_anns = [i for i, (_, (s, e)) in enumerate(c_anns)
                       if s <= x1 + EPS and e >= x2 - EPS]
        active_segs = [j for j, (_, (s, e)) in enumerate(c_segs)
                       if s <= x1 + EPS and e >= x2 - EPS]
        if not active_anns:
            continue
        length = x2 - x1
        if active_segs:
            for ai in active_anns:
                for sj in active_segs:
                    touch[(ai, sj)] = touch.get((ai, sj), 0.0) + length
        for pos, ai in enumerate(active_anns):
            for bj in active_anns[pos + 1:]:
                key = (min(ai, bj), max(ai, bj))
                pair_overlap.setdefault(key, []).append((x1, x2))
    return spans, c_anns, c_segs, touch, pair_overlap


def _out_of_bounds_issues(anns, coverage, window, explicit=False):
    """Out-of-bounds against the *global* segment coverage (whole document)."""
    win_lo, win_hi = window
    issues = []
    cov_start = coverage[0][0] if coverage else None
    cov_end = coverage[-1][1] if coverage else None
    for ann in anns:
        a0, a1 = ann["start"], ann["end"]
        if a1 - a0 <= EPS:
            continue
        if min(a1, win_hi) - max(a0, win_lo) <= EPS:
            continue  # annotation does not touch the scanned window
        covered = _intersect_ranges((a0, a1), coverage)
        uncovered = _subtract_ranges((a0, a1), coverage)
        if not uncovered:
            continue
        # when a window is given, the issue belongs to it only if some
        # uncovered time actually falls inside the window
        if explicit:
            windowed_uncovered = _intersect_ranges((win_lo, win_hi),
                                                   uncovered)
            if not windowed_uncovered:
                continue
        outside = any(e <= cov_start + EPS or s >= cov_end - EPS
                      for s, e in uncovered) if coverage else True
        if not covered:
            severity = SEVERITY_ERROR
            fixable = False
            message = (f"校注「{_topic(ann['raw'])}」完全位于转写覆盖范围之外"
                       f"（{_r3(a0)}s–{_r3(a1)}s），未覆盖任何转写片段")
            fix = None
        elif outside:
            severity = SEVERITY_ERROR
            fixable = True
            fix = {"start": _r3(covered[0][0]), "end": _r3(covered[-1][1])}
            message = (f"校注「{_topic(ann['raw'])}」越出转写边界："
                       f"{_r3(a0)}s–{_r3(a1)}s，转写覆盖 "
                       f"{_r3(cov_start)}s–{_r3(cov_end)}s，可裁剪到 "
                       f"{fix['start']}s–{fix['end']}s")
        else:
            # uncovered time lies strictly inside the transcript: gap spans
            severity = SEVERITY_INFO
            fixable = False
            fix = None
            total_gap = sum(e - s for s, e in uncovered)
            message = (f"校注「{_topic(ann['raw'])}」覆盖区间内有 "
                       f"{_r3(total_gap)}s 没有对应的转写片段（内部空隙）")
        issues.append({
            "issueId": f"qc:out_of_bounds:{ann['id']}",
            "type": TYPE_OUT_OF_BOUNDS,
            "severity": severity,
            "start": _r3(a0),
            "end": _r3(a1),
            "message": message,
            "annotationId": ann["id"],
            "annotationIds": [ann["id"]],
            "annotations": [_slim_ann(ann["raw"])],
            "evidence": {
                "uncoveredRanges": [{"start": _r3(s), "end": _r3(e)}
                                    for s, e in _merge_ranges(uncovered)],
                "coveredRanges": [{"start": _r3(s), "end": _r3(e)}
                                  for s, e in covered],
                "transcriptCoverage": (
                    {"start": _r3(cov_start), "end": _r3(cov_end)}
                    if coverage else None),
            },
            "fixable": fixable,
            "fix": fix,
        })
    return issues


def _overlap_issues(c_anns, pair_overlap):
    issues = []
    for (i, j), spans in pair_overlap.items():
        ranges = _merge_ranges(spans)
        ov_start, ov_end = ranges[0][0], ranges[-1][1]
        ov_seconds = ov_end - ov_start if len(ranges) == 1 else \
            sum(e - s for s, e in ranges)
        ai, _ = c_anns[i]
        bj, _ = c_anns[j]
        len_i = max(EPS, ai["end"] - ai["start"])
        len_j = max(EPS, bj["end"] - bj["start"])
        contained = (ai["start"] <= bj["start"] + EPS
                     and bj["end"] <= ai["end"] + EPS) or \
                    (bj["start"] <= ai["start"] + EPS
                     and ai["end"] <= bj["end"] + EPS)
        mostly = ov_seconds > 0.5 * min(len_i, len_j) + EPS
        severity = SEVERITY_ERROR if (contained or mostly) else SEVERITY_WARNING
        id_a, id_b = sorted([ai["id"], bj["id"]])
        by_id = {ai["id"]: ai, bj["id"]: bj}
        message = (f"两条校注时间重叠 {_r3(ov_seconds)}s："
                   f"「{_topic(by_id[id_a]['raw'])}」与"
                   f"「{_topic(by_id[id_b]['raw'])}」")
        issues.append({
            "issueId": f"qc:overlap:{id_a}|{id_b}",
            "type": TYPE_OVERLAP,
            "severity": severity,
            "start": _r3(ov_start),
            "end": _r3(ov_end),
            "message": message,
            "annotationId": None,
            "annotationIds": [id_a, id_b],
            "annotations": [_slim_ann(by_id[id_a]["raw"]),
                            _slim_ann(by_id[id_b]["raw"])],
            "evidence": {
                "overlapSeconds": _r3(ov_seconds),
                "ranges": [{"start": _r3(s), "end": _r3(e)}
                           for s, e in ranges],
            },
            "fixable": False,
            "fix": None,
        })
    return issues


def _speaker_issues(c_anns, c_segs, touch):
    """One (aggregated) speaker issue per annotation."""
    issues = []
    for ai, (ann, _) in enumerate(c_anns):
        touched = [(sj, dur) for (i, sj), dur in touch.items()
                   if i == ai and dur > EPS]
        if not touched:
            continue
        touched.sort(key=lambda item: (c_segs[item[0]][0]["start"],
                                       c_segs[item[0]][0]["id"]))
        ann_sp = (ann["raw"].get("speaker") or "").strip()
        seg_items = []
        seg_speakers = set()
        mismatches = []
        for sj, dur in touched:
            seg, _ = c_segs[sj]
            sp = (seg["raw"].get("speaker") or "").strip()
            seg_items.append(_slim_seg(seg["raw"], dur))
            if sp:
                seg_speakers.add(sp)
                if ann_sp and sp != ann_sp:
                    mismatches.append((seg, sp, dur))

        base = {
            "start": _r3(ann["start"]),
            "end": _r3(ann["end"]),
            "annotationId": ann["id"],
            "annotationIds": [ann["id"]],
            "annotations": [_slim_ann(ann["raw"])],
            "fixable": False,
            "fix": None,
        }
        if not ann_sp and seg_speakers:
            shown = "、".join(sorted(seg_speakers))
            issues.append({
                **base,
                "issueId": f"qc:speaker_missing:{ann['id']}",
                "type": TYPE_SPEAKER_MISSING,
                "severity": SEVERITY_INFO,
                "message": f"校注「{_topic(ann['raw'])}」未填写说话人，"
                           f"其覆盖的转写片段说话人为「{shown}」",
                "evidence": {"segments": seg_items,
                             "segmentSpeakers": sorted(seg_speakers)},
            })
            continue
        if mismatches:
            # every covered segment disagreeing with the annotation speaker
            # is a clear error; if some covered segments do agree, the
            # interval merely straddles a speaker change -> warning
            severity = (SEVERITY_WARNING if ann_sp in seg_speakers
                        else SEVERITY_ERROR)
            shown = "、".join(sorted({sp for _, sp, _ in mismatches}))
            mismatch_segs = [_slim_seg(seg["raw"], dur)
                             for seg, _, dur in mismatches]
            issues.append({
                **base,
                "issueId": f"qc:speaker_mismatch:{ann['id']}",
                "type": TYPE_SPEAKER_MISMATCH,
                "severity": severity,
                "message": f"校注「{_topic(ann['raw'])}」说话人为「{ann_sp}」，"
                           f"但其覆盖的 {len(mismatch_segs)} 个转写片段说话人"
                           f"为「{shown}」",
                "evidence": {"annotationSpeaker": ann_sp,
                             "segments": mismatch_segs,
                             "segmentSpeakers": sorted(seg_speakers)},
            })
    return issues


def check_document(document: dict,
                   start: float | None = None,
                   end: float | None = None) -> dict:
    """Run the quality check over a document (optionally one time window)."""
    document = document or {}
    if end is not None and start is not None and end <= start:
        raise ValueError("结束时间必须晚于开始时间")
    anns_all = _valid_items(document.get("annotations", []))
    segs_all = _valid_items(document.get("segments", []))
    window = _resolve_window(document, start, end)
    win_start, win_end, explicit = window

    coverage = _merge_ranges([(s["start"], s["end"]) for s in segs_all
                              if s["end"] - s["start"] > EPS])

    spans, c_anns, c_segs, touch, pair_overlap = _sweep(
        anns_all, segs_all, (win_start, win_end))

    issues = []
    issues += _out_of_bounds_issues(anns_all, coverage, (win_start, win_end),
                                    explicit)
    issues += _overlap_issues(c_anns, pair_overlap)
    issues += _speaker_issues(c_anns, c_segs, touch)

    issues.sort(key=lambda q: (SEVERITY_ORDER[q["severity"]],
                               q["start"], q["end"], q["issueId"]))

    def count(sev):
        return sum(1 for q in issues if q["severity"] == sev)

    def count_type(t):
        return sum(1 for q in issues if q["type"] == t)

    return {
        "window": {"start": _r3(win_start), "end": _r3(win_end)},
        "explicitWindow": explicit,
        "summary": {
            "total": len(issues),
            "error": count(SEVERITY_ERROR),
            "warning": count(SEVERITY_WARNING),
            "info": count(SEVERITY_INFO),
            "outOfBounds": count_type(TYPE_OUT_OF_BOUNDS),
            "overlap": count_type(TYPE_OVERLAP),
            "speakerMismatch": count_type(TYPE_SPEAKER_MISMATCH),
            "speakerMissing": count_type(TYPE_SPEAKER_MISSING),
            "annotations": len(anns_all),
            "segments": len(segs_all),
            "scanLines": spans,
            "fixable": sum(1 for q in issues if q.get("fixable")),
        },
        "issues": issues,
    }


def same_window(window_a: dict, window_b: dict, tol: float = 1e-6) -> bool:
    """Two scans are comparable only if their time windows are identical."""
    try:
        return (abs(float(window_a["start"]) - float(window_b["start"])) <= tol
                and abs(float(window_a["end"]) - float(window_b["end"])) <= tol)
    except (KeyError, TypeError, ValueError):
        return False


def compare_scans(base_scan: dict, target_scan: dict) -> dict:
    """Diff two snapshots of the *same* time window by stable issueId.

    Issues present only in ``target_scan`` are new, those only in
    ``base_scan`` were resolved, and issueIds present in both still exist.
    Comparing scans with different windows is an explicit error.
    """
    if not same_window(base_scan.get("window") or {},
                       target_scan.get("window") or {}):
        raise ValueError("两次扫描的时间窗口不同，无法比较；请选择时间窗口"
                         "（开始 / 结束）完全相同的两次扫描")
    base_issues = {q["issueId"]: q for q in base_scan.get("issues", [])}
    target_issues = {q["issueId"]: q for q in target_scan.get("issues", [])}
    new = [target_issues[i] for i in target_issues if i not in base_issues]
    resolved = [base_issues[i] for i in base_issues if i not in target_issues]
    persistent = [target_issues[i] for i in target_issues if i in base_issues]
    return {
        "window": target_scan.get("window"),
        "base": {"id": base_scan.get("id"),
                 "createdAt": base_scan.get("createdAt"),
                 "summary": base_scan.get("summary")},
        "target": {"id": target_scan.get("id"),
                   "createdAt": target_scan.get("createdAt"),
                   "summary": target_scan.get("summary")},
        "counts": {
            "new": len(new),
            "resolved": len(resolved),
            "persistent": len(persistent),
        },
        "newIssues": new,
        "resolvedIssues": resolved,
        "persistentIssues": persistent,
    }
