"""Speaker normalization engine.

Aggregates the speaker names currently used by a project's saved transcript
segments, annotations and annotation rules (their exact speaker match
condition), and plans a batch rename driven by user-configured mappings.

Mapping semantics:

* chains collapse to the final name — with ``A -> B`` and ``B -> C`` both
  configured, ``A`` is renamed to ``C`` in one pass;
* an empty target name blocks the operation (ValueError);
* a mapping cycle (including ``A -> A``) blocks the operation;
* names are compared after stripping surrounding whitespace, and
  whitespace-only speakers are treated as "no speaker" and ignored.
"""
from __future__ import annotations


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def collect_speakers(document: dict, rules: list[dict]) -> list[dict]:
    """Aggregate existing speaker names across the three sources.

    Returns entries ``{name, segments, annotations, rules, total}`` sorted by
    total usage (descending) and then by name.
    """
    counts: dict[str, dict] = {}

    def add(name: str, key: str) -> None:
        name = _clean(name)
        if not name:
            return
        entry = counts.setdefault(
            name, {"name": name, "segments": 0,
                   "annotations": 0, "rules": 0})
        entry[key] += 1

    for seg in document.get("segments", []) or []:
        add(seg.get("speaker"), "segments")
    for ann in document.get("annotations", []) or []:
        add(ann.get("speaker"), "annotations")
    for rule in rules or []:
        add(rule.get("speaker"), "rules")

    names = list(counts.values())
    for entry in names:
        entry["total"] = (entry["segments"] + entry["annotations"]
                          + entry["rules"])
    names.sort(key=lambda e: (-e["total"], e["name"]))
    return names


def resolve_mappings(mappings: list[dict]) -> dict[str, dict]:
    """Validate mappings and collapse chains to their final target.

    Returns ``{old_name: {"to": direct_target, "final": final_name,
    "chain": [old, ..., final]}}`` for every mapped source name.  Raises
    ValueError with a user-facing message on empty fields, duplicate sources
    or cycles (self mappings included).
    """
    direct: dict[str, str] = {}
    for raw in mappings or []:
        old = _clean(raw.get("from") if isinstance(raw, dict) else None)
        new = _clean(raw.get("to") if isinstance(raw, dict) else None)
        if not old and not new:
            continue  # completely empty row -> ignore
        if not old:
            raise ValueError("存在未填写旧名的映射行")
        if not new:
            raise ValueError(f"映射「{old}」的目标名为空，无法提交")
        if old in direct:
            raise ValueError(f"旧名「{old}」配置了多条映射，请只保留一条")
        direct[old] = new

    resolved: dict[str, dict] = {}
    for source in direct:
        chain = [source]
        seen = {source}
        cur = source
        while cur in direct:
            nxt = direct[cur]
            if nxt in seen:
                # locate where the cycle starts for a readable message
                loop = chain[chain.index(nxt):] + [nxt]
                raise ValueError(
                    "检测到循环映射：" + " → ".join(loop) + "，请先解除循环")
            chain.append(nxt)
            seen.add(nxt)
            cur = nxt
        resolved[source] = {"to": direct[source], "final": cur, "chain": chain}
    return resolved


def plan_rename(document: dict, rules: list[dict],
                mappings: list[dict]) -> dict:
    """Build the preview: every name's final destination and impact counts.

    Raises ValueError on invalid mappings (see :func:`resolve_mappings`).
    """
    resolved = resolve_mappings(mappings)
    names = collect_speakers(document, rules)
    plan_names = []
    totals = {"segments": 0, "annotations": 0, "rules": 0}
    renamed = 0
    for entry in names:
        info = resolved.get(entry["name"])
        final = info["final"] if info else entry["name"]
        changed = final != entry["name"]
        if changed:
            renamed += 1
            totals["segments"] += entry["segments"]
            totals["annotations"] += entry["annotations"]
            totals["rules"] += entry["rules"]
        plan_names.append({
            "name": entry["name"],
            "final": final,
            "changed": changed,
            "segments": entry["segments"],
            "annotations": entry["annotations"],
            "rules": entry["rules"],
            "total": entry["total"],
        })
    plan_mappings = [
        {"from": old, "to": info["to"], "final": info["final"],
         "chain": list(info["chain"])}
        for old, info in sorted(resolved.items())
    ]
    return {
        "names": plan_names,
        "mappings": plan_mappings,
        "summary": {
            "speakers": len(names),
            "renamed": renamed,
            "segments": totals["segments"],
            "annotations": totals["annotations"],
            "rules": totals["rules"],
        },
    }
