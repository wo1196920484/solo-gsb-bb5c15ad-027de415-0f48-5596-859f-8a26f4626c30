"""End-to-end API tests for rule-based batch annotation.

Run with: DATA_DIR=/tmp/x python -m pytest backend/test_rules_api.py -q
(or directly: python test_rules_api.py).  Not named test_*.py discovery
only — works under pytest as well.
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="rules-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

RULE = {
    "name": "受访者谈改制", "speaker": "受访者", "pattern": "改制",
    "matchType": "keyword", "caseSensitive": False, "minDuration": 2,
    "topic": "企业改制",
    "noteTemplate": "{speaker}在{segments}个片段提到{keyword}",
    "color": "#4cae8f",
}


def run():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "访谈A"}).json()
        pid = p["id"]
        doc = {
            "segments": [
                {"id": "s1", "start": 0, "end": 3, "speaker": "访谈者",
                 "text": "请问改制是哪年"},
                {"id": "s2", "start": 3, "end": 9, "speaker": "受访者",
                 "text": "2003年改制，厂里变化大"},
                {"id": "s3", "start": 9, "end": 14, "speaker": "受访者",
                 "text": "我还在车间"},
                {"id": "s4", "start": 14, "end": 15, "speaker": "受访者",
                 "text": "嗯"},
            ],
            "annotations": [
                {"id": "ann-manual", "start": 0, "end": 1, "speaker": "",
                 "topic": "手工", "note": "m"},
            ],
        }
        c.put(f"/api/projects/{pid}",
              json={"name": "访谈A", "document": doc,
                    "documentVersion": p["documentVersion"]})

        # draft preview, no persistence
        pv = c.post(f"/api/projects/{pid}/rules/preview",
                    json={"rule": RULE}).json()
        assert pv["stats"]["willCreate"] == 1, pv["stats"]
        assert pv["hits"][0]["segmentIds"] == ["s2"]
        assert pv["hits"][0]["annotation"]["note"] == "受访者在1个片段提到改制"
        assert c.get(f"/api/projects/{pid}/rules").json() == []

        # invalid regex -> 400, nothing stored
        bad = c.post(f"/api/projects/{pid}/rules",
                     json={**RULE, "matchType": "regex", "pattern": "("})
        assert bad.status_code == 400, bad.text

        # save + preview + apply
        r = c.post(f"/api/projects/{pid}/rules", json=RULE).json()
        rid = r["id"]
        assert c.post(f"/api/rules/{rid}/preview").json()["stats"][
            "willCreate"] == 1
        ap = c.post(f"/api/rules/{rid}/apply").json()
        ids = [a["id"] for a in ap["document"]["annotations"]]
        assert "ann-manual" in ids
        assert any(i.startswith("ann-rule-") for i in ids)
        assert ap["run"]["createdCount"] == 1
        assert ap["run"]["updatedCount"] == 0
        stored = c.get(f"/api/projects/{pid}").json()
        rule_anns = [a for a in stored["document"]["annotations"]
                     if a.get("ruleId") == rid]
        assert rule_anns[0]["source"] == "rule"
        assert rule_anns[0]["topic"] == "企业改制"

        # re-apply updates the same annotations, manual untouched
        ap2 = c.post(f"/api/rules/{rid}/apply").json()
        assert ap2["run"]["createdCount"] == 0
        assert ap2["run"]["updatedCount"] == 1
        assert len(ap2["document"]["annotations"]) == 2

        # detach (manual edit) -> re-apply keeps manual + writes new rule
        # output.  ownership is lost by removing `source`; ruleId/ruleSlot
        # remain as provenance (mirrors the editor page behavior).
        cur_proj = c.get(f"/api/projects/{pid}").json()
        cur = cur_proj["document"]
        rule_ann = next(a for a in cur["annotations"]
                        if a.get("ruleId") == rid)
        rule_ann.pop("source")
        rule_ann.pop("ruleName")
        rule_ann["topic"] = "手工改过"
        c.put(f"/api/projects/{pid}",
              json={"name": "访谈A", "document": cur,
                    "documentVersion": cur_proj["documentVersion"]})
        ap3 = c.post(f"/api/rules/{rid}/apply").json()
        topics = sorted(a["topic"] for a in ap3["document"]["annotations"])
        assert topics == ["企业改制", "手工", "手工改过"], topics
        assert ap3["run"]["keptManualCount"] == 1

        # undo only this application
        u = c.post(f"/api/rule-runs/{ap3['run']['id']}/undo").json()
        topics_u = sorted(a["topic"] for a in u["document"]["annotations"])
        assert topics_u == ["手工", "手工改过"], topics_u
        assert c.post(f"/api/rule-runs/{ap3['run']['id']}/undo").status_code \
            == 409

        # manual deletion -> tombstone: re-apply must not resurrect
        ap4 = c.post(f"/api/rules/{rid}/apply").json()
        cur2 = ap4["document"]
        cur2["annotations"] = [a for a in cur2["annotations"]
                               if a.get("ruleId") != rid]
        ver2 = c.get(f"/api/projects/{pid}").json()["documentVersion"]
        c.put(f"/api/projects/{pid}",
              json={"name": "访谈A", "document": cur2,
                    "documentVersion": ver2})
        pv3 = c.post(f"/api/rules/{rid}/preview").json()
        assert pv3["stats"]["willCreate"] == 0
        assert pv3["stats"]["suppressed"] == 1
        ap5 = c.post(f"/api/rules/{rid}/apply").json()
        assert all(a.get("ruleId") != rid
                   for a in ap5["document"]["annotations"])

        # undo that run, then apply again: the manual deletion was undone,
        # so regeneration is allowed.
        c.post(f"/api/rule-runs/{ap5['run']['id']}/undo")
        ap6 = c.post(f"/api/rules/{rid}/apply").json()
        assert len([a for a in ap6["document"]["annotations"]
                    if a.get("ruleId") == rid]) == 1

        # changed rule groups adjacent matches and drops short segments
        c.put(f"/api/rules/{rid}",
             json={**RULE, "matchType": "none", "minDuration": 2})
        pv5 = c.post(f"/api/rules/{rid}/preview").json()
        assert pv5["stats"]["groups"] == 1, pv5["stats"]
        h0 = pv5["hits"][0]["annotation"]
        assert h0["start"] == 3 and h0["end"] == 14, (h0["start"], h0["end"])

        # export still serves and contains provenance
        exp = c.get(f"/api/projects/{pid}/export")
        assert "attachment" in exp.headers["content-disposition"]

        # cascade on project deletion
        p2 = c.post("/api/projects", json={"name": "B"}).json()
        r2 = c.post(f"/api/projects/{p2['id']}/rules", json=RULE).json()
        c.post(f"/api/rules/{r2['id']}/apply")
        assert len(c.get(f"/api/projects/{pid}/rules").json()) == 1
        c.delete(f"/api/projects/{p2['id']}")
        assert c.get(f"/api/rules/{r2['id']}").status_code == 404

        # deleting a rule detaches its annotations and drops runs
        c.delete(f"/api/rules/{rid}")
        cur3 = c.get(f"/api/projects/{pid}").json()["document"]
        assert all(a.get("source") != "rule"
                   for a in cur3["annotations"])
        assert c.get(f"/api/projects/{pid}/rule-runs").json() == []

        # regex rule with alternation
        rr = c.post(f"/api/projects/{pid}/rules", json={
            **RULE, "name": "R", "matchType": "regex",
            "pattern": "改制|车间"}).json()
        pvr = c.post(f"/api/rules/{rr['id']}/preview").json()
        assert pvr["stats"]["matchedSegments"] == 2, pvr["stats"]

        print("ALL E2E PASSED")


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
