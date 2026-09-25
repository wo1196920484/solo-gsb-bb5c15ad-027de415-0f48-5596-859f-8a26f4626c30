"""End-to-end API tests for the speaker normalization workbench.

Run with: python3 test_speakers_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

import pytest

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="speaker-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.speakers import collect_speakers, plan_rename, resolve_mappings  # noqa: E402

DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 5, "speaker": "李明",
         "text": "我叫李明"},
        {"id": "s2", "start": 5, "end": 8, "speaker": "老李", "text": "嗯"},
        {"id": "s3", "start": 8, "end": 12, "speaker": "王主任",
         "text": "请坐"},
        {"id": "s4", "start": 12, "end": 14, "speaker": "   ",
         "text": "（无声）"},
    ],
    "annotations": [
        {"id": "a1", "start": 0, "end": 5, "speaker": "李明",
         "topic": "背景", "note": "备注一",
         "source": "rule", "ruleId": "rule-x", "ruleSlot": 0},
        {"id": "a2", "start": 8, "end": 12, "speaker": "王主任",
         "topic": "开场"},
    ],
}


def _rules(speaker):
    return [
        {"name": f"规则-{speaker}-1", "speaker": speaker,
         "matchType": "none", "pattern": "", "topic": "t",
         "noteTemplate": ""},
        {"name": f"规则-{speaker}-2", "speaker": speaker,
         "matchType": "none", "pattern": "", "topic": "t",
         "noteTemplate": ""},
    ]


# ---------- engine ----------

def test_collect_speakers():
    rules = [{"speaker": "老李"}, {"speaker": "老李"}, {"speaker": "  "},
             {"speaker": ""}]
    names = {n["name"]: n for n in collect_speakers(DOC, rules)}
    assert names["李明"] == {"name": "李明", "segments": 1,
                            "annotations": 1, "rules": 0, "total": 2}
    assert names["老李"]["rules"] == 2
    assert names["老李"]["total"] == 3
    assert names["王主任"]["total"] == 2
    # whitespace-only speakers are "no speaker", never aggregated
    assert all(n["name"].strip() for n in names.values())
    # sorted by total desc then name
    assert [n["name"] for n in collect_speakers(DOC, rules)] == \
        ["老李", "李明", "王主任"]


def test_resolve_mappings_chains_and_validation():
    # simple + chained: 李明 -> 老李 -> 李 collapses 李明 to 李
    res = resolve_mappings([{"from": "李明", "to": "老李"},
                            {"from": "老李", "to": "李"}])
    assert res["李明"]["to"] == "老李"
    assert res["李明"]["final"] == "李"
    assert res["李明"]["chain"] == ["李明", "老李", "李"]
    assert res["老李"]["final"] == "李"

    # surrounding whitespace is stripped
    res = resolve_mappings([{"from": " 李明 ", "to": " 李 "},
                            {"from": "李", "to": "李教授"}])
    assert res["李明"]["final"] == "李教授"

    # completely empty rows are ignored
    assert resolve_mappings([]) == {}
    assert resolve_mappings([{"from": "", "to": "  "}]) == {}

    for bad, needle in (
        ([{"from": "李明", "to": ""}], "目标名为空"),
        ([{"from": "  ", "to": "李"}], "未填写旧名"),
        ([{"from": "李明", "to": "李"},
          {"from": "李明", "to": "王"}], "多条映射"),
        # direct cycle, including a self mapping
        ([{"from": "A", "to": "A"}], "循环映射"),
        # indirect cycle
        ([{"from": "A", "to": "B"}, {"from": "B", "to": "C"},
          {"from": "C", "to": "B"}], "循环映射"),
    ):
        with pytest.raises(ValueError) as exc:
            resolve_mappings(bad)
        assert needle in str(exc.value)


def test_plan_rename():
    plan = plan_rename(
        DOC, [{"speaker": "老李"}, {"speaker": "老李"}],
        [{"from": "李明", "to": "老李"}, {"from": "老李", "to": "李"}])
    by_name = {n["name"]: n for n in plan["names"]}
    assert by_name["李明"]["final"] == "李" and by_name["李明"]["changed"]
    assert by_name["老李"]["final"] == "李" and by_name["老李"]["changed"]
    assert not by_name["王主任"]["changed"]
    assert by_name["王主任"]["final"] == "王主任"
    assert plan["summary"] == {"speakers": 3, "renamed": 2,
                               "segments": 2, "annotations": 1, "rules": 2}
    chain = {m["from"]: m for m in plan["mappings"]}
    assert chain["李明"]["chain"] == ["李明", "老李", "李"]


# ---------- API ----------

def test_api():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "说话人归一访谈"}).json()
        pid = p["id"]
        c.put(f"/api/projects/{pid}",
              json={"name": "说话人归一访谈", "document": DOC,
                    "documentVersion": p["documentVersion"]})
        rule_ids = []
        for payload in _rules("老李") + [
            {"name": "规则-无说话人", "speaker": "", "matchType": "none",
             "pattern": "", "topic": "t", "noteTemplate": ""},
        ]:
            r = c.post(f"/api/projects/{pid}/rules", json=payload)
            assert r.status_code == 201, r.text
            rule_ids.append(r.json()["id"])

        # aggregation preview with no mappings yet
        r = c.post(f"/api/projects/{pid}/speaker-rename/preview",
                   json={"mappings": []})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert [n["name"] for n in data["names"]] == ["老李", "李明", "王主任"]
        li = next(n for n in data["names"] if n["name"] == "老李")
        assert li["rules"] == 2 and li["total"] == 3
        assert all(not n["changed"] for n in data["names"])
        assert data["summary"]["speakers"] == 3

        # chain preview: 李明 -> 老李 -> 李
        r = c.post(f"/api/projects/{pid}/speaker-rename/preview",
                   json={"mappings": [
                       {"from": "李明", "to": "老李"},
                       {"from": "老李", "to": "李"}]})
        assert r.status_code == 200, r.text
        data = r.json()
        by_name = {n["name"]: n for n in data["names"]}
        assert by_name["李明"]["final"] == "李"
        assert by_name["老李"]["final"] == "李"
        assert data["summary"] == {"speakers": 3, "renamed": 2,
                                   "segments": 2, "annotations": 1,
                                   "rules": 2}

        # invalid mappings are blocked before any write
        assert c.post(f"/api/projects/{pid}/speaker-rename/preview",
                      json={"mappings": [{"from": "李明", "to": ""}]}
                      ).status_code == 400
        assert c.post(f"/api/projects/{pid}/speaker-rename",
                      json={"mappings": [{"from": "李明", "to": "老李"},
                                          {"from": "老李", "to": "李明"}]}
                      ).status_code == 400
        assert c.post(f"/api/projects/{pid}/speaker-rename",
                      json={"mappings": [{"from": "李明", "to": "李明"}]}
                      ).status_code == 400
        assert c.post("/api/projects/nope/speaker-rename",
                      json={"mappings": []}).status_code == 404
        # nothing changed after the blocked attempts
        still = c.get(f"/api/projects/{pid}").json()
        assert [s["speaker"] for s in still["document"]["segments"]] == \
            ["李明", "老李", "王主任", "   "]

        # commit
        r = c.post(f"/api/projects/{pid}/speaker-rename",
                   json={"mappings": [
                       {"from": "李明", "to": "老李"},
                       {"from": "老李", "to": "李"}]})
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["counts"] == {"segments": 2, "annotations": 1,
                                   "rules": 2}
        saved = result["project"]["document"]
        segs = {s["id"]: s for s in saved["segments"]}
        anns = {a["id"]: a for a in saved["annotations"]}
        # speakers rewritten, ids and every other field kept
        assert segs["s1"]["speaker"] == "李"
        assert segs["s2"]["speaker"] == "李"
        assert segs["s3"]["speaker"] == "王主任"
        assert segs["s4"]["speaker"] == "   "  # blank speaker untouched
        assert segs["s1"]["text"] == "我叫李明"
        assert (segs["s1"]["start"], segs["s1"]["end"]) == (0, 5)
        assert anns["a1"]["speaker"] == "李"
        assert anns["a1"]["topic"] == "背景"
        assert anns["a1"]["note"] == "备注一"
        # rule provenance fields are preserved
        assert anns["a1"]["ruleId"] == "rule-x"
        assert anns["a1"]["ruleSlot"] == 0
        assert anns["a1"]["source"] == "rule"
        assert anns["a2"]["speaker"] == "王主任"
        # rule speaker conditions rewritten too
        rules = {r["id"]: r for r in c.get(
            f"/api/projects/{pid}/rules").json()}
        assert all(rules[rid]["speaker"] == "李" for rid in rule_ids[:2])
        assert rules[rule_ids[2]]["speaker"] == ""

        # downstream panels read the new names: quote search by new speaker
        r = c.post(f"/api/projects/{pid}/quote-search",
                   json={"query": "我叫李明", "speaker": "李"})
        assert r.status_code == 200
        assert r.json()["count"] == 1
        r = c.post(f"/api/projects/{pid}/quote-search",
                   json={"query": "我叫李明", "speaker": "李明"})
        assert r.json()["count"] == 0


def test_rename_is_atomic_on_failure():
    """A failure while writing rolls back document *and* rules together."""
    with TestClient(app) as c:
        p = c.post("/api/projects", json={"name": "事务回滚"}).json()
        pid = p["id"]
        c.put(f"/api/projects/{pid}",
              json={"name": "事务回滚", "document": DOC,
                    "documentVersion": p["documentVersion"]})
        r = c.post(f"/api/projects/{pid}/rules",
                   json=_rules("老李")[0])
        rid = r.json()["id"]

        # a non-serializable target makes the document UPDATE raise midway;
        # the transaction must roll back with no partial modification
        with pytest.raises(TypeError):
            db.rename_speakers(pid, {"李明": object()})

        after = c.get(f"/api/projects/{pid}").json()
        assert [s["speaker"] for s in after["document"]["segments"]] == \
            ["李明", "老李", "王主任", "   "]
        assert c.get(f"/api/rules/{rid}").json()["speaker"] == "老李"

        # database is still healthy: a valid rename works afterwards
        r = c.post(f"/api/projects/{pid}/speaker-rename",
                   json={"mappings": [{"from": "王主任", "to": "王"}]})
        assert r.status_code == 200, r.text
        seg = next(s for s in r.json()["project"]["document"]["segments"]
                   if s["id"] == "s3")
        assert seg["speaker"] == "王"


def run():
    test_collect_speakers()
    test_resolve_mappings_chains_and_validation()
    test_plan_rename()
    test_api()
    test_rename_is_atomic_on_failure()
    print("ALL SPEAKER NORMALIZATION TESTS PASSED")


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
