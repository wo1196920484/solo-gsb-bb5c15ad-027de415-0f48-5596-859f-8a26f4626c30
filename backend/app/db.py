"""SQLite persistence (stdlib sqlite3, one file under DATA_DIR)."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from . import corrections
from . import rules as rule_engine

_DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(_DATA_DIR, "annotation.db")

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_us() -> str:
    """Microsecond precision, so scans within one second stay ordered."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def document_version(document: dict | None) -> str:
    """Stable content fingerprint of a whole project document.

    SHA-256 over the canonical JSON (sorted keys, tight separators) of the
    entire document — every segment and annotation field is covered, so any
    content change flips the version even when two saves land within the
    same second (the second-precision ``updated_at`` cannot detect that).
    The same document always yields the same version, so re-saving
    unchanged content never looks like a conflict.
    """
    canonical = json.dumps(document or {}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def init() -> None:
    global _conn
    os.makedirs(_DATA_DIR, exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            document    TEXT NOT NULL DEFAULT '{"segments":[],"annotations":[]}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS annotation_rules (
            id             TEXT PRIMARY KEY,
            project_id     TEXT NOT NULL,
            name           TEXT NOT NULL,
            speaker        TEXT NOT NULL DEFAULT '',
            pattern        TEXT NOT NULL DEFAULT '',
            match_type     TEXT NOT NULL DEFAULT 'keyword',
            case_sensitive INTEGER NOT NULL DEFAULT 0,
            min_duration   REAL NOT NULL DEFAULT 0,
            topic          TEXT NOT NULL DEFAULT '',
            note_template  TEXT NOT NULL DEFAULT '',
            color          TEXT NOT NULL DEFAULT '#5b8def',
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rule_runs (
            id                 TEXT PRIMARY KEY,
            rule_id            TEXT NOT NULL,
            project_id         TEXT NOT NULL,
            created_count      INTEGER NOT NULL DEFAULT 0,
            updated_count      INTEGER NOT NULL DEFAULT 0,
            removed_count      INTEGER NOT NULL DEFAULT 0,
            kept_manual_count  INTEGER NOT NULL DEFAULT 0,
            generated_slots    TEXT NOT NULL DEFAULT '[]',
            snapshot           TEXT NOT NULL DEFAULT '[]',
            status             TEXT NOT NULL DEFAULT 'applied',
            created_at         TEXT NOT NULL,
            FOREIGN KEY (rule_id) REFERENCES annotation_rules(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS quality_scans (
            id              TEXT PRIMARY KEY,
            project_id      TEXT NOT NULL,
            window_start    REAL NOT NULL,
            window_end      REAL NOT NULL,
            explicit_window INTEGER NOT NULL DEFAULT 0,
            summary         TEXT NOT NULL DEFAULT '{}',
            issues          TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_rules_project
            ON annotation_rules(project_id);
        CREATE INDEX IF NOT EXISTS idx_runs_rule ON rule_runs(rule_id);
        CREATE INDEX IF NOT EXISTS idx_runs_project ON rule_runs(project_id);
        CREATE INDEX IF NOT EXISTS idx_qc_scans_project
            ON quality_scans(project_id);
        """
    )
    _conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    document = json.loads(row["document"])
    return {
        "id": row["id"],
        "name": row["name"],
        "document": document,
        "documentVersion": document_version(document),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_projects() -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_project(project_id: str) -> dict | None:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def create_project(name: str, document: dict | None = None) -> dict:
    pid = new_id("proj")
    now = _now()
    doc = document if document is not None else {"segments": [], "annotations": []}
    with _lock:
        _conn.execute(
            "INSERT INTO projects (id, name, document, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (pid, name, json.dumps(doc, ensure_ascii=False), now, now),
        )
        _conn.commit()
    return get_project(pid)


def update_project(project_id: str, name: str | None, document: dict,
                   expected_version: str | None = None) -> dict | None:
    """Update a project's document (and optionally its name).

    When ``expected_version`` is given, the stored document's version is
    re-computed and compared inside the same write lock: a mismatch means
    another writer saved first (e.g. corrections confirmed on another
    page), so nothing is written — not even the name — and
    ``{"conflict": True}`` is returned.  Returns ``None`` when the project
    does not exist.
    """
    now = _now()
    with _lock:
        if expected_version is not None:
            row = _conn.execute(
                "SELECT document FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row is None:
                return None
            current = document_version(json.loads(row["document"]))
            if current != expected_version:
                return {"conflict": True}
        if name is not None:
            _conn.execute(
                "UPDATE projects SET name = ?, document = ?, updated_at = ?"
                " WHERE id = ?",
                (name, json.dumps(document, ensure_ascii=False), now, project_id),
            )
        else:
            _conn.execute(
                "UPDATE projects SET document = ?, updated_at = ? WHERE id = ?",
                (json.dumps(document, ensure_ascii=False), now, project_id),
            )
        _conn.commit()
    return get_project(project_id)


def delete_project(project_id: str) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        _conn.commit()
    return cur.rowcount > 0


def rename_speakers(project_id: str, resolved: dict) -> dict | None:
    """Rewrite speaker names in segments, annotations and rules at once.

    ``resolved`` maps an existing (stripped) name to its final target —
    chains must already be collapsed by the engine.  Segment / annotation
    ids and every other field are kept; only the ``speaker`` field changes.
    The document update and all rule updates happen in one transaction, so a
    failure rolls everything back and leaves no partial modification.
    Returns the refreshed project plus per-source change counts.
    """
    counts = {"segments": 0, "annotations": 0, "rules": 0}
    with _lock:
        project_row = _conn.execute(
            "SELECT document FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project_row is None:
            return None
        doc = json.loads(project_row["document"])
        now = _now()
        try:
            _conn.execute("BEGIN")
            for seg in doc.get("segments", []) or []:
                key = seg.get("speaker").strip() \
                    if isinstance(seg.get("speaker"), str) else ""
                if key in resolved:
                    seg["speaker"] = resolved[key]
                    counts["segments"] += 1
            for ann in doc.get("annotations", []) or []:
                key = ann.get("speaker").strip() \
                    if isinstance(ann.get("speaker"), str) else ""
                if key in resolved:
                    ann["speaker"] = resolved[key]
                    counts["annotations"] += 1
            _conn.execute(
                "UPDATE projects SET document = ?, updated_at = ? WHERE id = ?",
                (json.dumps(doc, ensure_ascii=False), now, project_id),
            )
            for old_name, new_name in resolved.items():
                cur = _conn.execute(
                    "UPDATE annotation_rules SET speaker = ?, updated_at = ?"
                    " WHERE project_id = ? AND speaker = ?",
                    (new_name, now, project_id, old_name),
                )
                counts["rules"] += cur.rowcount
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise
    return {"project": get_project(project_id), "counts": counts}


# ---------- annotation rules ----------

def _rule_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "speaker": row["speaker"],
        "pattern": row["pattern"],
        "matchType": row["match_type"],
        "caseSensitive": bool(row["case_sensitive"]),
        "minDuration": row["min_duration"],
        "topic": row["topic"],
        "noteTemplate": row["note_template"],
        "color": row["color"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _run_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "ruleId": row["rule_id"],
        "projectId": row["project_id"],
        "createdCount": row["created_count"],
        "updatedCount": row["updated_count"],
        "removedCount": row["removed_count"],
        "keptManualCount": row["kept_manual_count"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


def rule_payload(data: dict) -> dict:
    """Normalize a rule payload from an API schema into column values."""
    return {
        "name": data["name"].strip(),
        "speaker": (data.get("speaker") or "").strip(),
        "pattern": (data.get("pattern") or "").strip(),
        "match_type": data.get("matchType") or rule_engine.MATCH_KEYWORD,
        "case_sensitive": 1 if data.get("caseSensitive") else 0,
        "min_duration": float(data.get("minDuration") or 0.0),
        "topic": data.get("topic") or "",
        "note_template": data.get("noteTemplate") or "",
        "color": data.get("color") or rule_engine.DEFAULT_COLOR,
    }


def _last_run_for_rules(rule_ids: list[str]) -> dict[str, dict]:
    """Most recent run (any status) per rule id."""
    if not rule_ids:
        return {}
    out: dict[str, dict] = {}
    with _lock:
        rows = _conn.execute(
            f"SELECT * FROM rule_runs WHERE rule_id IN "
            f"({','.join('?' for _ in rule_ids)}) ORDER BY created_at ASC",
            rule_ids,
        ).fetchall()
    for row in rows:
        out[row["rule_id"]] = _run_to_dict(row)  # ASC -> last one wins
    return out


def list_rules(project_id: str) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM annotation_rules WHERE project_id = ?"
            " ORDER BY created_at",
            (project_id,),
        ).fetchall()
    rules = [_rule_to_dict(r) for r in rows]
    last_runs = _last_run_for_rules([r["id"] for r in rules])
    for rule in rules:
        rule["lastRun"] = last_runs.get(rule["id"])
    return rules


def get_rule(rule_id: str) -> dict | None:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM annotation_rules WHERE id = ?", (rule_id,)
        ).fetchone()
    return _rule_to_dict(row) if row else None


def create_rule(project_id: str, data: dict) -> dict:
    values = rule_payload(data)
    rid = new_id("rule")
    now = _now()
    with _lock:
        _conn.execute(
            "INSERT INTO annotation_rules (id, project_id, name, speaker,"
            " pattern, match_type, case_sensitive, min_duration, topic,"
            " note_template, color, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, project_id, values["name"], values["speaker"],
             values["pattern"], values["match_type"], values["case_sensitive"],
             values["min_duration"], values["topic"], values["note_template"],
             values["color"], now, now),
        )
        _conn.commit()
    return get_rule(rid)


def update_rule(rule_id: str, data: dict) -> dict | None:
    values = rule_payload(data)
    now = _now()
    with _lock:
        cur = _conn.execute(
            "UPDATE annotation_rules SET name = ?, speaker = ?, pattern = ?,"
            " match_type = ?, case_sensitive = ?, min_duration = ?, topic = ?,"
            " note_template = ?, color = ?, updated_at = ? WHERE id = ?",
            (values["name"], values["speaker"], values["pattern"],
             values["match_type"], values["case_sensitive"],
             values["min_duration"], values["topic"], values["note_template"],
             values["color"], now, rule_id),
        )
        _conn.commit()
    if cur.rowcount == 0:
        return None
    return get_rule(rule_id)


def delete_rule(rule_id: str) -> bool:
    """Delete a rule; annotations it generated become plain manual ones."""
    with _lock:
        row = _conn.execute(
            "SELECT project_id FROM annotation_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        if row is None:
            return False
        project = _conn.execute(
            "SELECT document FROM projects WHERE id = ?", (row["project_id"],)
        ).fetchone()
        if project is not None:
            doc = json.loads(project["document"])
            changed = False
            for ann in doc.get("annotations", []):
                if rule_engine.is_owned(ann, rule_id):
                    # detach ownership while keeping ruleId/ruleSlot as
                    # provenance (same semantics as a manual edit)
                    ann.pop("source", None)
                    ann.pop("ruleName", None)
                    changed = True
            if changed:
                _conn.execute(
                    "UPDATE projects SET document = ?, updated_at = ?"
                    " WHERE id = ?",
                    (json.dumps(doc, ensure_ascii=False), _now(),
                     row["project_id"]),
                )
        _conn.execute("DELETE FROM annotation_rules WHERE id = ?", (rule_id,))
        _conn.commit()
    return True


# ---------- rule runs (apply / undo) ----------

def list_rule_runs(project_id: str) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM rule_runs WHERE project_id = ?"
            " ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    return [_run_to_dict(r) for r in rows]


def rule_tombstones(rule_id: str) -> set[int]:
    """Slots from the latest applied run whose output has since vanished.

    Only slots whose annotation is gone from the document count (manual
    deletion); a rule output that was detached by a manual edit is still
    present and therefore protected by id collision, not a tombstone.
    """
    with _lock:
        run_row = _conn.execute(
            "SELECT r.generated_slots, p.document FROM rule_runs r"
            " JOIN projects p ON p.id = r.project_id"
            " WHERE r.rule_id = ? AND r.status = 'applied'"
            " ORDER BY r.created_at DESC LIMIT 1",
            (rule_id,),
        ).fetchone()
    if run_row is None:
        return set()
    slots = set(json.loads(run_row["generated_slots"]))
    doc = json.loads(run_row["document"])
    # present if an annotation still carries this rule's slot provenance,
    # including one the user detached from the rule by manual editing; only
    # slots with no surviving annotation at all are manual deletions.
    present_slots = {
        a.get("ruleSlot") for a in doc.get("annotations", [])
        if a.get("ruleId") == rule_id
    }
    return slots - present_slots


def get_rule_run(run_id: str) -> dict | None:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM rule_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return _run_to_dict(row) if row else None


def apply_rule_run(rule_id: str) -> dict | None:
    """Apply (or re-apply) a rule to its project, atomically.

    Only annotations currently owned by this rule (source/ruleId) are
    replaced; manual annotations and other rules' output are untouched.
    Every previously active run of the same rule is marked superseded.
    """
    with _lock:
        rule_row = _conn.execute(
            "SELECT * FROM annotation_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        if rule_row is None:
            return None
        project_row = _conn.execute(
            "SELECT * FROM projects WHERE id = ?", (rule_row["project_id"],)
        ).fetchone()
        doc = json.loads(project_row["document"])
        rule = _rule_to_dict(rule_row)
        existing = doc.get("annotations", [])

        # Manual deletions since the last run must not be resurrected.
        tombstones = rule_tombstones(rule_id)
        plan = rule_engine.plan_apply(rule, rule_id, doc, tombstones)

        owned = plan["owned"]
        snapshot = [deepcopy_json(a) for a in
                    sorted(owned, key=lambda a: (
                        float(a.get("start", 0.0)), str(a.get("id", ""))))]
        owned_ids = {a.get("id") for a in owned}
        kept = [a for a in existing if a.get("id") not in owned_ids]
        doc["annotations"] = kept + plan["proposed"]
        doc["annotations"].sort(
            key=lambda a: (float(a.get("start", 0.0)), str(a.get("id", ""))))

        stats = plan["stats"]
        now = _now()
        _conn.execute(
            "UPDATE projects SET document = ?, updated_at = ? WHERE id = ?",
            (json.dumps(doc, ensure_ascii=False), now, project_row["id"]),
        )
        _conn.execute(
            "UPDATE rule_runs SET status = 'superseded'"
            " WHERE rule_id = ? AND status = 'applied'",
            (rule_id,),
        )
        run_id = new_id("run")
        _conn.execute(
            "INSERT INTO rule_runs (id, rule_id, project_id, created_count,"
            " updated_count, removed_count, kept_manual_count,"
            " generated_slots, snapshot, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?)",
            (run_id, rule_id, project_row["id"], stats["willCreate"],
             stats["willUpdate"], stats["willRemove"], stats["keptManual"],
             json.dumps([a["ruleSlot"] for a in plan["proposed"]]),
             json.dumps(snapshot, ensure_ascii=False), now),
        )
        _conn.commit()
        run = get_rule_run(run_id)
        return {"run": run, "document": doc}


def undo_rule_run(run_id: str) -> dict | None:
    """Revert one rule application.

    Removes the annotations owned by that run and restores the prior owned
    annotations captured in the run's snapshot.  Manual annotations
    (including rule output that the user detached by editing) are left
    untouched.
    """
    with _lock:
        row = _conn.execute(
            "SELECT * FROM rule_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        if row["status"] != "applied":
            raise ValueError("该次应用已撤销或已被新的应用取代，无法撤销")
        project_row = _conn.execute(
            "SELECT * FROM projects WHERE id = ?", (row["project_id"],)
        ).fetchone()
        doc = json.loads(project_row["document"])
        snapshot = json.loads(row["snapshot"])
        snapshot_ids = {a.get("id") for a in snapshot}
        kept = [a for a in doc.get("annotations", [])
                if not (rule_engine.is_owned(a, row["rule_id"])
                        and a.get("id") not in snapshot_ids)]
        existing_ids = {a.get("id") for a in kept}
        restored = []
        for ann in snapshot:
            if ann.get("id") in existing_ids:
                # the id is now occupied by a manually detached annotation
                continue
            restored.append(ann)
            existing_ids.add(ann.get("id"))
        doc["annotations"] = kept + restored
        doc["annotations"].sort(
            key=lambda a: (float(a.get("start", 0.0)), str(a.get("id", ""))))
        _conn.execute(
            "UPDATE projects SET document = ?, updated_at = ? WHERE id = ?",
            (json.dumps(doc, ensure_ascii=False), _now(), row["project_id"]),
        )
        _conn.execute(
            "UPDATE rule_runs SET status = 'undone' WHERE id = ?", (run_id,))
        _conn.commit()
        return {"run": get_rule_run(run_id), "document": doc}


def deepcopy_json(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def apply_corrections(project_id: str, pairs: list[dict],
                      expected_version: str) -> dict | None:
    """Atomically rewrite corrected segment texts in the saved document.

    The transcript version is re-verified inside the write lock: when the
    saved transcript was edited or re-imported after the preview, nothing
    is written and ``{"conflict": True}`` is returned (the caller asks for
    a fresh preview).  Only the ``text`` field of hit segments changes —
    segment ids, times, speakers and every annotation are preserved, and
    the single document update commits or rolls back as a whole.
    ``pairs`` must already be normalized by the corrections engine.
    Returns ``None`` when the project does not exist.
    """
    with _lock:
        row = _conn.execute(
            "SELECT document FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        doc = json.loads(row["document"])
        segments = doc.get("segments", []) or []
        if corrections.transcript_version(segments) != expected_version:
            return {"conflict": True}
        new_texts, replacements = corrections.corrected_texts(segments, pairs)
        changed_ids: list = []
        for index, new_text in new_texts.items():
            seg = segments[index]
            if isinstance(seg, dict) and seg.get("text") != new_text:
                seg["text"] = new_text
                changed_ids.append(seg.get("id"))
        if changed_ids:
            now = _now()
            try:
                _conn.execute("BEGIN")
                _conn.execute(
                    "UPDATE projects SET document = ?, updated_at = ?"
                    " WHERE id = ?",
                    (json.dumps(doc, ensure_ascii=False), now, project_id),
                )
                _conn.commit()
            except Exception:
                _conn.rollback()
                raise
    return {
        "project": get_project(project_id),
        "counts": {"segments": len(changed_ids), "replacements": replacements},
        "changedSegmentIds": changed_ids,
    }


# ---------- quality-check scan history ----------

def _scan_list_to_dict(row: sqlite3.Row) -> dict:
    """Scan summary row (without the full issue snapshot)."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "window": {"start": row["window_start"], "end": row["window_end"]},
        "explicitWindow": bool(row["explicit_window"]),
        "summary": json.loads(row["summary"]),
        "createdAt": row["created_at"],
    }


def _scan_detail_to_dict(row: sqlite3.Row) -> dict:
    data = _scan_list_to_dict(row)
    data["issues"] = json.loads(row["issues"])
    return data


def create_quality_scan(project_id: str, result: dict) -> dict:
    """Persist one quality check: time window, stats and issue snapshot."""
    scan_id = new_id("scan")
    now = _now_us()
    window = result.get("window") or {}
    with _lock:
        _conn.execute(
            "INSERT INTO quality_scans (id, project_id, window_start,"
            " window_end, explicit_window, summary, issues, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (scan_id, project_id, float(window.get("start", 0.0)),
             float(window.get("end", 0.0)),
             1 if result.get("explicitWindow") else 0,
             json.dumps(result.get("summary") or {}, ensure_ascii=False),
             json.dumps(result.get("issues") or [], ensure_ascii=False),
             now),
        )
        _conn.commit()
    return get_quality_scan(project_id, scan_id)


def list_quality_scans(project_id: str) -> list[dict]:
    """All scans of a project, newest first (summaries, no snapshots)."""
    with _lock:
        rows = _conn.execute(
            "SELECT id, project_id, window_start, window_end,"
            " explicit_window, summary, created_at FROM quality_scans"
            " WHERE project_id = ? ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
    return [_scan_list_to_dict(r) for r in rows]


def get_quality_scan(project_id: str, scan_id: str) -> dict | None:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM quality_scans WHERE id = ? AND project_id = ?",
            (scan_id, project_id),
        ).fetchone()
    return _scan_detail_to_dict(row) if row else None
