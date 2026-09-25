"""End-to-end tests for project document versioning (optimistic locking).

Every project read returns a stable content fingerprint (``version``);
the editor's PUT must carry it back as ``baseVersion``.  The server
re-checks the version inside the write lock: a mismatch rejects the
whole save with 409 and no field (name, segment, annotation, …) is
written.

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
         "text": "记录完整无误"},
    ],
    "annotations": [
        {"id": "a1", "start": 0, "end": 5, "speaker": "受访者",
         "topic": "开场", "note": ""},
    ],
}


# ---------- fingerprint engine ----------

def test_document_version_is_stable_and_content_based():
    v1 = db.document_version(DOC)
    assert isinstance(v1, str) and len(v1) == 64
    assert db.document_version(copy.deepcopy(DOC)) == v1
    # JSON key order does not matter -> a real content fingerprint
    reordered = {"annotations": copy.deepcopy(DOC["annotations"]),
                 "segments": copy.deepcopy(DOC["segments"])}
    assert db.document_version(reordered) == v1
    # an empty document is a fixed, non-null version
    assert db.document_version({"segments": [], "annotations": []})
    assert db.document_version(None) == db.document_version({})


def test_version_covers_every_document_field():
    base = db.document_version(DOC)

    def altered(mutate):
        d = copy.deepcopy(DOC)
        mutate(d)
        return db.document_version(d)

    # any segment field change is visible
    assert altered(lambda d: d["segments"][0].update(text="改一个字")) != base
    assert altered(lambda d: d["segments"][0].update(speaker="别人")) != base
    assert altered(lambda d: d["segments"][0].update(end=5.5)) != base
    assert altered(lambda d: d["segments"].pop(0)) != base
    assert altered(lambda d: d["segments"].append(
        {"id": "s9", "start": 9, "end": 10, "speaker": "", "text": "新"})) \
        != base
    # annotations are covered too (whole document, not only the transcript)
    assert altered(lambda d: d["annotations"][0].update(note="加备注")) != base
    assert altered(lambda d: d["annotations"][0].update(end=4)) != base
    assert altered(lambda d: d["annotations"].pop(0)) != base


# ---------- API ----------

def _create_with_doc(c, name="版本测试", document=DOC):
    project = c.post("/api/projects", json={"name": name}).json()
    assert "version" in project
    r = c.put(f"/api/projects/{project['id']}",
              json={"document": copy.deepcopy(document),
                    "baseVersion": project["version"]})
    assert r.status_code == 200, r.text
    return project["id"]


def test_reads_include_version():
    with TestClient(app) as c:
        pid = _create_with_doc(c)
        got = c.get(f"/api/projects/{pid}").json()
        assert got["version"] == db.document_version(DOC)
        # the list endpoint carries versions as well
        listed = [p for p in c.get("/api/projects").json() if p["id"] == pid]
        assert len(listed) == 1 and listed[0]["version"] == got["version"]


def test_save_without_version_is_rejected():
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "缺版本"}).json()
        r = c.put(f"/api/projects/{project['id']}",
                  json={"document": DOC})
        assert r.status_code == 400
        assert "版本" in r.json()["detail"]
        r = c.put(f"/api/projects/{project['id']}",
                  json={"document": DOC, "baseVersion": "   "})
        assert r.status_code == 400
        # nothing was written: still the empty document as created
        saved = c.get(f"/api/projects/{project['id']}").json()["document"]
        assert saved == {"segments": [], "annotations": []}
        # unknown project -> 404 even with a version
        r = c.put("/api/projects/nope",
                  json={"document": DOC, "baseVersion": "x"})
        assert r.status_code == 404


def test_stale_version_conflicts_and_writes_nothing():
    with TestClient(app) as c:
        pid = _create_with_doc(c, name="原文")
        before = c.get(f"/api/projects/{pid}").json()

        # a different writer saves first (corrections apply, another tab…)
        other_doc = copy.deepcopy(DOC)
        other_doc["segments"][1]["text"] = "另一页确认的错词校正"
        first = c.put(
            f"/api/projects/{pid}",
            json={"document": other_doc, "baseVersion": before["version"]})
        assert first.status_code == 200, first.text
        newest_version = first.json()["version"]

        # the stale editor saves: it carries the old read-time version and
        # even tries to rename the project -> 409, and NOTHING is written
        stale_doc = copy.deepcopy(DOC)
        stale_doc["annotations"][0]["topic"] = "旧页面的修改"
        r = c.put(f"/api/projects/{pid}",
                  json={"name": "旧页面想改名", "document": stale_doc,
                        "baseVersion": before["version"]})
        assert r.status_code == 409
        assert "重新加载" in r.json()["detail"]

        after = c.get(f"/api/projects/{pid}").json()
        assert after["version"] == newest_version
        assert after["name"] == "原文"  # the rejected name never landed
        assert after["document"] == other_doc  # other page's edit preserved
        assert after["document"]["segments"][1]["text"] \
            == "另一页确认的错词校正"
        # the rejected save did not even touch updated_at
        assert after["updatedAt"] == first.json()["updatedAt"]


def test_two_editors_reload_then_save_flow():
    with TestClient(app) as c:
        pid = _create_with_doc(c)
        # both tabs load the same version
        tab_a = c.get(f"/api/projects/{pid}").json()
        tab_b = c.get(f"/api/projects/{pid}").json()
        assert tab_a["version"] == tab_b["version"]

        # tab A saves confirmed corrections
        doc_a = copy.deepcopy(DOC)
        doc_a["segments"][1]["text"] = "A 的校正"
        saved_a = c.put(f"/api/projects/{pid}",
                        json={"document": doc_a,
                              "baseVersion": tab_a["version"]})
        assert saved_a.status_code == 200

        # tab B's save with the stale version is refused; its draft text is
        # kept client-side, and the server still holds A's correction
        doc_b = copy.deepcopy(DOC)
        doc_b["annotations"][0]["topic"] = "B 的校注"
        refused = c.put(f"/api/projects/{pid}",
                        json={"document": doc_b,
                              "baseVersion": tab_b["version"]})
        assert refused.status_code == 409
        assert c.get(f"/api/projects/{pid}").json()[
            "document"]["segments"][1]["text"] == "A 的校正"

        # B explicitly reloads the latest project, then saves again
        reloaded_b = c.get(f"/api/projects/{pid}").json()
        merged = reloaded_b["document"]
        merged["annotations"][0]["topic"] = "B 的校注"  # B re-applies its edit
        saved_b = c.put(f"/api/projects/{pid}",
                        json={"document": merged,
                              "baseVersion": reloaded_b["version"]})
        assert saved_b.status_code == 200, saved_b.text
        final = c.get(f"/api/projects/{pid}").json()
        assert final["document"]["segments"][1]["text"] == "A 的校正"
        assert final["document"]["annotations"][0]["topic"] == "B 的校注"
        assert final["version"] == saved_b.json()["version"]

        # the version on the success response cannot be reused after a
        # further external write
        extra = copy.deepcopy(final["document"])
        extra["segments"][0]["text"] = "再改"
        c.put(f"/api/projects/{pid}",
              json={"document": extra, "baseVersion": final["version"]})
        stale_again = c.put(f"/api/projects/{pid}",
                            json={"document": final["document"],
                                  "baseVersion": final["version"]})
        assert stale_again.status_code == 409


def test_version_changes_on_every_save_even_back_to_back():
    """Content-based versions cannot collide within the same second."""
    with TestClient(app) as c:
        pid = _create_with_doc(c)
        current = c.get(f"/api/projects/{pid}").json()
        seen = {current["version"]}
        for i in range(3):
            next_doc = copy.deepcopy(DOC)
            next_doc["segments"][0]["text"] = f"连续保存 {i}"
            r = c.put(f"/api/projects/{pid}",
                      json={"document": next_doc,
                            "baseVersion": current["version"]})
            assert r.status_code == 200, r.text
            current = r.json()
            assert current["version"] not in seen
            seen.add(current["version"])


def test_internal_unversioned_update_still_works():
    """Internal writers (import / merge write-back) bypass the guard."""
    with TestClient(app) as c:
        pid = _create_with_doc(c)
        updated = db.update_project(pid, None, copy.deepcopy(DOC))
        assert updated is not None and updated["id"] == pid
        # an explicit guard still conflicts afterwards at the db layer
        gone = db.update_project(
            pid, None, {"segments": [], "annotations": []},
            expected_version="definitely-not-a-real-version")
        assert gone == {"conflict": True}
        # unknown projects report None (the API maps that to 404)
        assert db.update_project("missing-project", None, DOC,
                                 expected_version="v") is None


def run():
    test_document_version_is_stable_and_content_based()
    test_version_covers_every_document_field()
    test_reads_include_version()
    test_save_without_version_is_rejected()
    test_stale_version_conflicts_and_writes_nothing()
    test_two_editors_reload_then_save_flow()
    test_version_changes_on_every_save_even_back_to_back()
    test_internal_unversioned_update_still_works()
    print("ALL PROJECT VERSION TESTS PASSED")


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
