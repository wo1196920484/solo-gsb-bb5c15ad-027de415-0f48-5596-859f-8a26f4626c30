"""End-to-end API tests for the project document version (optimistic lock).

Every project read carries a stable ``documentVersion`` fingerprint of the
whole document; the editor saves with the version it read and the backend
re-checks it inside the write lock.  A stale save (another page saved in
the meantime — e.g. confirmed error-word corrections) is rejected with
409 and nothing is written, so the old page must reload before saving
again.

Run with: python3 test_project_version_api.py  (or via pytest).
"""
import copy
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="project-version-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 5, "speaker": "访谈者",
         "text": "请谈谈你的感受"},
        {"id": "s2", "start": 5, "end": 9, "speaker": "受访者",
         "text": "等的候，他讲的很好"},
    ],
    "annotations": [
        {"id": "a1", "start": 0, "end": 9, "speaker": "受访者",
         "topic": "工厂经历", "note": "重点"},
    ],
}


# ---------- version fingerprint ----------

def test_document_version_is_stable_and_content_based():
    v = db.document_version(DOC)
    assert db.document_version(DOC) == v
    # key order inside the document does not matter
    shuffled = {
        "annotations": list(reversed(DOC["annotations"])),
        "segments": [
            {k: seg[k] for k in reversed(list(seg))} for seg in DOC["segments"]
        ],
    }
    assert db.document_version(shuffled) == v
    # an empty / missing document still has a deterministic version
    assert db.document_version(None) == db.document_version({})


def test_document_version_covers_every_document_field():
    base = db.document_version(DOC)
    for mutate in (
        lambda d: d["segments"][0].update(text="改一个字"),
        lambda d: d["segments"][0].update(speaker="别人"),
        lambda d: d["segments"][1].update(end=9.5),
        lambda d: d["annotations"][0].update(topic="新主题"),
        lambda d: d["annotations"][0].update(note=""),
        lambda d: d["segments"].append(
            {"id": "s3", "start": 9, "end": 10, "speaker": "", "text": "新"}),
        lambda d: d["annotations"].pop(0),
    ):
        altered = copy.deepcopy(DOC)
        mutate(altered)
        assert db.document_version(altered) != base


# ---------- API ----------

def _new_project(c, name="版本访谈", document=None):
    """Create a project (optionally seeding a document); return id+version."""
    p = c.post("/api/projects", json={"name": name}).json()
    pid = p["id"]
    if document is None:
        return pid, p["documentVersion"]
    r = c.put(f"/api/projects/{pid}",
              json={"name": name, "document": document,
                    "documentVersion": p["documentVersion"]})
    assert r.status_code == 200, r.text
    return pid, r.json()["documentVersion"]


def test_project_reads_carry_a_stable_document_version():
    with TestClient(app) as c:
        pid, v0 = _new_project(c, document=DOC)
        again = c.get(f"/api/projects/{pid}").json()
        assert again["documentVersion"] == v0
        assert v0 == db.document_version(DOC)
        # the project list exposes the same version
        listed = next(p for p in c.get("/api/projects").json()
                      if p["id"] == pid)
        assert listed["documentVersion"] == v0


def test_save_requires_version_and_rotates_it():
    with TestClient(app) as c:
        pid, v0 = _new_project(c, document=DOC)

        # missing version -> 400, nothing written
        r = c.put(f"/api/projects/{pid}", json={"document": DOC})
        assert r.status_code == 400
        assert "版本" in r.json()["detail"]
        assert c.get(f"/api/projects/{pid}").json()["documentVersion"] == v0

        edited = copy.deepcopy(DOC)
        edited["segments"][0]["text"] = "编辑页改过的文本"
        r = c.put(f"/api/projects/{pid}",
                  json={"document": edited, "documentVersion": v0})
        assert r.status_code == 200, r.text
        v1 = r.json()["documentVersion"]
        assert v1 and v1 != v0
        saved = c.get(f"/api/projects/{pid}").json()
        assert saved["document"]["segments"][0]["text"] == "编辑页改过的文本"
        assert saved["documentVersion"] == v1

        # unknown project -> 404 even with a version supplied
        assert c.put("/api/projects/nope",
                     json={"document": DOC, "documentVersion": v1}
                     ).status_code == 404


def test_identical_resave_is_not_a_false_conflict():
    """Re-saving unchanged content keeps the version (no false conflict)."""
    with TestClient(app) as c:
        pid, v0 = _new_project(c, document=DOC)
        r = c.put(f"/api/projects/{pid}",
                  json={"document": DOC, "documentVersion": v0})
        assert r.status_code == 200, r.text
        assert r.json()["documentVersion"] == v0


def test_stale_editor_save_conflicts_and_writes_nothing():
    """Two pages on one project: the older save must not clobber the newer."""
    with TestClient(app) as c:
        pid, v0 = _new_project(c, document=DOC)

        # page B confirms a wrong-word correction (its own locked write)
        preview = c.post(f"/api/projects/{pid}/corrections/preview",
                         json={"pairs": [{"from": "的候", "to": "时候"}]}
                         ).json()
        r = c.post(f"/api/projects/{pid}/corrections/apply",
                   json={"pairs": [{"from": "的候", "to": "时候"}],
                         "transcriptVersion": preview["transcriptVersion"]})
        assert r.status_code == 200, r.text

        # page A (the old editor, still holding v0) saves the whole document
        stale_doc = copy.deepcopy(DOC)
        stale_doc["segments"][0]["text"] = "旧编辑页的整份覆盖"
        r = c.put(f"/api/projects/{pid}",
                  json={"name": "被旧页改的名", "document": stale_doc,
                        "documentVersion": v0})
        assert r.status_code == 409
        assert "重新加载" in r.json()["detail"]

        # nothing was written: neither the document nor the project name
        saved = c.get(f"/api/projects/{pid}").json()
        assert saved["name"] == "版本访谈"
        texts = [s["text"] for s in saved["document"]["segments"]]
        assert "等时候，他讲的很好" in texts        # correction kept
        assert "旧编辑页的整份覆盖" not in texts    # stale save rejected

        # after reloading, the editor saves with the fresh version
        reloaded_doc = saved["document"]
        reloaded_doc["segments"][1]["text"] = "等时候，他讲的很好（已核对）"
        r = c.put(f"/api/projects/{pid}",
                  json={"document": reloaded_doc,
                        "documentVersion": saved["documentVersion"]})
        assert r.status_code == 200, r.text
        final = c.get(f"/api/projects/{pid}").json()["document"]
        assert final["segments"][1]["text"] == "等时候，他讲的很好（已核对）"


def test_same_second_updates_are_still_detected():
    """updated_at has second precision; the version must not rely on it."""
    with TestClient(app) as c:
        pid, v0 = _new_project(c, document=DOC)
        original_now = db._now
        db._now = lambda: "2026-09-25T12:00:00+00:00"  # freeze the clock
        try:
            edited = copy.deepcopy(DOC)
            edited["segments"][0]["speaker"] = "主持人"
            r = c.put(f"/api/projects/{pid}",
                      json={"document": edited, "documentVersion": v0})
            assert r.status_code == 200, r.text
            saved = c.get(f"/api/projects/{pid}").json()
            # same second, identical updated_at — yet the version moved
            assert saved["updatedAt"] == "2026-09-25T12:00:00+00:00"
            assert saved["documentVersion"] != v0
            # a stale save from the same second is still rejected
            r = c.put(f"/api/projects/{pid}",
                      json={"document": DOC, "documentVersion": v0})
            assert r.status_code == 409
        finally:
            db._now = original_now


def test_import_and_rule_apply_move_the_version():
    """Any write path moves the version, so old editor saves conflict."""
    with TestClient(app) as c:
        pid, v0 = _new_project(c, document=DOC)

        # re-import replaces the transcript (annotations preserved)
        r = c.post(f"/api/projects/{pid}/import",
                   json={"data": [{"id": "n1", "start": 0, "end": 2,
                                   "speaker": "甲", "text": "重新导入的"}]})
        assert r.status_code == 200, r.text
        v1 = r.json()["documentVersion"]  # the import response carries it
        assert v1 != v0
        r = c.put(f"/api/projects/{pid}",
                  json={"document": DOC, "documentVersion": v0})
        assert r.status_code == 409

        # a rule application (its own transaction) also moves the version
        r = c.post(f"/api/projects/{pid}/rules", json={
            "name": "标记甲", "speaker": "甲", "matchType": "none",
            "topic": "甲的发言"})
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        r = c.post(f"/api/rules/{rid}/apply")
        assert r.status_code == 200, r.text
        v2 = c.get(f"/api/projects/{pid}").json()["documentVersion"]
        assert v2 != v1
        r = c.put(f"/api/projects/{pid}",
                  json={"document": DOC, "documentVersion": v1})
        assert r.status_code == 409


def run():
    test_document_version_is_stable_and_content_based()
    test_document_version_covers_every_document_field()
    test_project_reads_carry_a_stable_document_version()
    test_save_requires_version_and_rotates_it()
    test_identical_resave_is_not_a_false_conflict()
    test_stale_editor_save_conflicts_and_writes_nothing()
    test_same_second_updates_are_still_detected()
    test_import_and_rule_apply_move_the_version()
    print("ALL PROJECT VERSION TESTS PASSED")


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
