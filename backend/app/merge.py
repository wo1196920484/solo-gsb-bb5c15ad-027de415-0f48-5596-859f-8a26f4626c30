"""Three-way merge for annotated transcript documents.

A document is {"segments": [...], "annotations": [...]}, every item carries
a stable client-generated ``id``.  Two editors branch from a common *base*,
edit offline, and their copies are merged:

* changes touching different items are merged automatically;
* when both sides change the same item differently -> ``identity`` conflict;
* modify-vs-delete of the same item -> ``identity`` conflict;
* annotations independently added by the two sides whose intervals overlap
  are grouped (connected components of the overlap graph) into one
  ``interval`` conflict;
* each conflict is adjudicated per item; resolutions may pick a side,
  supply a custom value, drop the item(s), or (interval only) keep all.
"""
from __future__ import annotations

from copy import deepcopy

SEG_KEY = "segments"
ANN_KEY = "annotations"

SEG_FIELDS = ("start", "end", "speaker", "text")
ANN_FIELDS = ("start", "end", "speaker", "topic", "note")
_ITEM_FIELDS = {SEG_KEY: SEG_FIELDS, ANN_KEY: ANN_FIELDS}


def _index(items):
    return {it["id"]: it for it in (items or [])}


def _fields(kind):
    return _ITEM_FIELDS[kind]


def _equal(x, y, kind):
    f = _fields(kind)
    return {k: x.get(k) for k in f} == {k: y.get(k) for k in f}


def _overlap(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def _identity_conflict(kind, item_id, base, a, b, ctype):
    """Both sides diverged on the same id."""
    fields = _fields(kind)
    if ctype == "modify":
        conflicting = []
        partial = {"id": item_id}
        for f in fields:
            av, bv, bv0 = a.get(f), b.get(f), base.get(f)
            a_changed, b_changed = av != bv0, bv != bv0
            if a_changed and b_changed and av != bv:
                conflicting.append(f)
            elif a_changed:
                partial[f] = av
            elif b_changed:
                partial[f] = bv
            else:
                partial[f] = bv0
        if not conflicting:
            return None, partial  # field-level edits don't overlap
        changed = sorted(
            f for f in fields
            if a.get(f) != base.get(f) or b.get(f) != base.get(f)
        )
        conf = {
            "id": f"{kind}:{item_id}:{ctype}",
            "kind": kind,
            "conflictType": "identity",
            "itemId": item_id,
            "modifyDelete": False,
            "base": deepcopy(base),
            "a": deepcopy(a),
            "b": deepcopy(b),
            "changedFields": changed,
            "conflictingFields": conflicting,
            "partialMerge": partial,
            "resolution": None,
        }
        return conf, None

    survivor = a if a is not None else b
    changed = sorted(f for f in fields if survivor.get(f) != base.get(f))
    return {
        "id": f"{kind}:{item_id}:{ctype}",
        "kind": kind,
        "conflictType": "identity",
        "itemId": item_id,
        "modifyDelete": True,
        "base": deepcopy(base),
        "a": deepcopy(a),
        "b": deepcopy(b),
        "changedFields": changed,
        "conflictingFields": changed,
        "partialMerge": None,
        "resolution": None,
    }, None


def _interval_clusters(a_added, b_added):
    """Connected components of the bipartite overlap graph."""
    adj_a = {i: set() for i in range(len(a_added))}
    adj_b = {j: set() for j in range(len(b_added))}
    for i, a in enumerate(a_added):
        for j, b in enumerate(b_added):
            if _overlap(a, b):
                adj_a[i].add(j)
                adj_b[j].add(i)

    seen_a, seen_b = set(), set()
    clusters = []
    for start_i in range(len(a_added)):
        if start_i in seen_a or not adj_a[start_i]:
            continue
        a_ids, b_ids = set(), set()
        stack = [("a", start_i)]
        while stack:
            side, idx = stack.pop()
            if side == "a":
                if idx in seen_a:
                    continue
                seen_a.add(idx)
                a_ids.add(idx)
                stack.extend(("b", j) for j in adj_a[idx])
            else:
                if idx in seen_b:
                    continue
                seen_b.add(idx)
                b_ids.add(idx)
                stack.extend(("a", i) for i in adj_b[idx])
        clusters.append((sorted(a_ids), sorted(b_ids)))
    return clusters


def analyze(base_doc, a_doc, b_doc):
    """Return (changes, conflicts, stats)."""
    base_doc, a_doc, b_doc = base_doc or {}, a_doc or {}, b_doc or {}
    changes = []
    conflicts = []
    stats = {
        "segments": {"added": 0, "modified": 0, "deleted": 0},
        "annotations": {"added": 0, "modified": 0, "deleted": 0},
        "conflicts": 0,
    }

    for kind in (SEG_KEY, ANN_KEY):
        base_idx = _index(base_doc.get(kind, []))
        a_idx = _index(a_doc.get(kind, []))
        b_idx = _index(b_doc.get(kind, []))
        a_added, b_added = [], []

        for item_id in sorted(set(base_idx) | set(a_idx) | set(b_idx)):
            base, a, b = (d.get(item_id) for d in (base_idx, a_idx, b_idx))
            in_b, in_a, in_bb = base is not None, a is not None, b is not None

            if in_b and in_a and in_bb:
                if a == b:
                    if not _equal(base, a, kind):
                        stats[kind]["modified"] += 1
                        changes.append(_ch(kind, item_id, "modified", a))
                    else:
                        changes.append(_ch(kind, item_id, "unchanged", a))
                elif _equal(base, a, kind):
                    stats[kind]["modified"] += 1
                    changes.append(_ch(kind, item_id, "modified", b))
                elif _equal(base, b, kind):
                    stats[kind]["modified"] += 1
                    changes.append(_ch(kind, item_id, "modified", a))
                else:
                    conf, partial = _identity_conflict(
                        kind, item_id, base, a, b, "modify")
                    if conf is None:
                        # both sides edited, but disjoint fields -> field merge
                        stats[kind]["modified"] += 1
                        changes.append(_ch(kind, item_id, "modified", partial))
                    else:
                        conflicts.append(conf)
                        changes.append(_conflict_ch(kind, item_id, conf))

            elif in_b and in_a:  # B deleted
                if _equal(base, a, kind):
                    stats[kind]["deleted"] += 1
                    changes.append(_ch(kind, item_id, "deleted", None))
                else:
                    conf, _ = _identity_conflict(
                        kind, item_id, base, a, None, "modifyDelete")
                    conflicts.append(conf)
                    changes.append(_conflict_ch(kind, item_id, conf))

            elif in_b and in_bb:  # A deleted
                if _equal(base, b, kind):
                    stats[kind]["deleted"] += 1
                    changes.append(_ch(kind, item_id, "deleted", None))
                else:
                    conf, _ = _identity_conflict(
                        kind, item_id, base, None, b, "modifyDelete")
                    conflicts.append(conf)
                    changes.append(_conflict_ch(kind, item_id, conf))

            elif in_b:  # both deleted
                stats[kind]["deleted"] += 1
                changes.append(_ch(kind, item_id, "deleted", None))

            elif in_a and in_bb:  # both added with the same id
                if a == b:
                    stats[kind]["added"] += 1
                    changes.append(_ch(kind, item_id, "added", a))
                else:
                    conf = {
                        "id": f"{kind}:{item_id}:addadd",
                        "kind": kind,
                        "conflictType": "identity",
                        "itemId": item_id,
                        "modifyDelete": False,
                        "base": None,
                        "a": deepcopy(a),
                        "b": deepcopy(b),
                        "changedFields": sorted(
                            f for f in _fields(kind) if a.get(f) != b.get(f)
                        ),
                        "conflictingFields": sorted(
                            f for f in _fields(kind) if a.get(f) != b.get(f)
                        ),
                        "partialMerge": None,
                        "resolution": None,
                    }
                    conflicts.append(conf)
                    changes.append(_conflict_ch(kind, item_id, conf))

            elif in_a:
                stats[kind]["added"] += 1
                a_added.append(a)
                changes.append(_ch(kind, item_id, "added", a, side="a"))
            elif in_bb:
                stats[kind]["added"] += 1
                b_added.append(b)
                changes.append(_ch(kind, item_id, "added", b, side="b"))

        if kind == ANN_KEY:
            for n, (ai, bj) in enumerate(_interval_clusters(a_added, b_added)):
                a_items = [deepcopy(a_added[i]) for i in ai]
                b_items = [deepcopy(b_added[j]) for j in bj]
                anchor = a_items[0]["id"]
                conf = {
                    "id": f"{ANN_KEY}:interval:{n}:{anchor}",
                    "kind": ANN_KEY,
                    "conflictType": "interval",
                    "itemId": None,
                    "aItems": a_items,
                    "bItems": b_items,
                    "base": None,
                    "a": a_items[0],
                    "b": b_items[0],
                    "changedFields": ["start", "end"],
                    "resolution": None,
                }
                conflicts.append(conf)
                changes.append({
                    "kind": ANN_KEY,
                    "action": "conflict",
                    "itemId": None,
                    "conflictId": conf["id"],
                    "aItems": deepcopy(a_items),
                    "bItems": deepcopy(b_items),
                })

    stats["conflicts"] = len(conflicts)
    return changes, conflicts, stats


def _ch(kind, item_id, action, merged, side=None):
    ch = {"kind": kind, "itemId": item_id, "action": action,
          "merged": deepcopy(merged)}
    if side:
        ch["side"] = side
    return ch


def _conflict_ch(kind, item_id, conf):
    return {
        "kind": kind, "itemId": item_id, "action": "conflict",
        "conflictId": conf["id"],
        "base": deepcopy(conf["base"]),
        "a": deepcopy(conf["a"]),
        "b": deepcopy(conf["b"]),
    }


def build_merged(changes, conflicts, resolutions):
    """Materialize the merged document from changes + adjudications.

    resolutions: {conflictId: {"choice": ..., "value"?: item}}
      identity conflict: a | b | custom | drop
      interval conflict: a | b | custom | keep | drop
    Returns (doc, unresolved_ids, errors).
    """
    resolutions = resolutions or {}
    unresolved = [c["id"] for c in conflicts if c["id"] not in resolutions]
    errors = []

    items = {}  # (kind, itemId) -> item or None
    for ch in changes:
        if ch["action"] != "conflict":
            items[(ch["kind"], ch["itemId"])] = ch.get("merged")

    for c in conflicts:
        res = resolutions.get(c["id"])
        if res is None:
            continue
        try:
            _apply_resolution(c, res, items)
        except ValueError as exc:
            errors.append({"conflictId": c["id"], "message": str(exc)})

    doc = {SEG_KEY: [], ANN_KEY: []}
    for (kind, _id), value in items.items():
        if value is not None:
            doc[kind].append(value)
    for key in (SEG_KEY, ANN_KEY):
        doc[key].sort(key=lambda x: (x.get("start", 0.0), str(x.get("id", ""))))
    return doc, unresolved, errors


def _apply_resolution(conf, res, items):
    kind = conf["kind"]
    choice = res.get("choice")

    if conf["conflictType"] == "interval":
        a_ids = {x["id"] for x in conf["aItems"]}
        b_ids = {x["id"] for x in conf["bItems"]}
        if choice == "a":
            for i in b_ids:
                items.pop((ANN_KEY, i), None)
        elif choice == "b":
            for i in a_ids:
                items.pop((ANN_KEY, i), None)
        elif choice == "keep":
            pass  # retain every item, overlap accepted
        elif choice == "drop":
            for i in a_ids | b_ids:
                items.pop((ANN_KEY, i), None)
        elif choice == "custom":
            value = _validate_custom(conf, res.get("value") or {})
            for i in a_ids | b_ids:
                items.pop((ANN_KEY, i), None)
            items[(ANN_KEY, value["id"])] = value
        else:
            raise ValueError(f"未知裁决方式: {choice!r}")
        return

    item_id = conf["itemId"]
    fields = _fields(kind)
    if choice in ("a", "b"):
        value = conf[choice]
        if value is None:
            items.pop((kind, item_id), None)
        else:
            items[(kind, item_id)] = deepcopy(value)
    elif choice == "drop":
        items.pop((kind, item_id), None)
    elif choice == "custom":
        value = _validate_custom(conf, res.get("value") or {})
        items[(kind, value["id"])] = value
    elif choice == "fields":
        # per-field adjudication: res["fields"] = {fieldName: "a"|"b"};
        # fields not listed there come from the automatic partial merge.
        chosen_fields = res.get("fields") or {}
        partial = dict(conf.get("partialMerge") or {})
        for f in fields:
            side = chosen_fields.get(f)
            if side in ("a", "b"):
                src = conf[side]
                if src is not None:
                    partial[f] = src.get(f)
            elif f not in partial:
                src = conf["a"] if conf["a"] is not None else conf["b"]
                partial[f] = (src or {}).get(f)
        partial = _validate_custom(conf, partial)
        items[(kind, item_id)] = partial
    else:
        raise ValueError(f"未知裁决方式: {choice!r}")


def _validate_custom(conf, value):
    kind = conf["kind"]
    fields = _fields(kind)
    if conf["conflictType"] == "interval":
        item_id = value.get("id") or f"custom-{conf['id']}"
    else:
        item_id = conf["itemId"]
    clean = {"id": item_id}
    for f in fields:
        if f not in value:
            raise ValueError(f"缺少字段 {f}")
        v = value[f]
        if f in ("start", "end"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{f} 必须是数字（秒）")
            if v < 0:
                raise ValueError(f"{f} 不能为负")
        elif f == "text" and kind == SEG_KEY:
            if not isinstance(v, str) or not v.strip():
                raise ValueError("text 不能为空")
            v = v.strip()
        else:
            v = "" if v is None else v
            if not isinstance(v, str):
                raise ValueError(f"{f} 必须是字符串")
        clean[f] = v
    if clean["end"] < clean["start"]:
        raise ValueError("end 不能早于 start")
    return clean


def merge_documents(base_doc, a_doc, b_doc, resolutions=None):
    """Analyze + build in one call."""
    changes, conflicts, stats = analyze(base_doc, a_doc, b_doc)
    doc, unresolved, errors = build_merged(changes, conflicts, resolutions)
    return {
        "document": doc,
        "changes": changes,
        "conflicts": conflicts,
        "stats": stats,
        "unresolved": unresolved,
        "errors": errors,
    }
