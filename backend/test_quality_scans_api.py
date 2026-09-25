"""End-to-end API tests for quality-check scan history & scan comparison.

Run with: python3 test_quality_scans_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="quality-scans-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

OOB_ID = "qc:out_of_bounds:ann-oob"
OV_ID = "qc:overlap:ann-a|ann-b"
SP_ID = "qc:speaker_mismatch:ann-sp"

DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 5,
         "speaker": "访谈者", "text": "开场"},
        {"id": "s2", "start": 5, "end": 10,
         "speaker": "访谈者", "text": "提问"},
        {"id": "s3", "start": 15, "end": 20,
         "speaker": "受访者", "text": "改制"},
    ],
    "annotations": [
        {"id": "ann-oob", "start": 18, "end": 30,
         "speaker": "受访者", "topic": "越界条"},
        {"id": "ann-a", "start": 0, "end": 5,
         "speaker": "访谈者", "topic": "重叠甲"},
        {"id": "ann-b", "start": 1, "end": 4,
         "speaker": "访谈者", "topic": "重叠乙"},
        {"id": "ann-sp", "start": 15, "end": 18,
         "speaker": "访谈者", "topic": "说错话人"},
    ],
}


def run():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "历史访谈"}).json()
        pid = p["id"]
        ver = c.put(f"/api/projects/{pid}",
                    json={"name": "历史访谈", "document": DOC,
                          "documentVersion": p["documentVersion"]}
                    ).json()["documentVersion"]

        # a second project proves scans are listed per project
        p2 = c.post("/api/projects", json={"name": "其他项目"}).json()
        c.post(f"/api/projects/{p2['id']}/quality-check")

        # --- every scan is persisted and returned with an id -------------
        r1 = c.post(f"/api/projects/{pid}/quality-check").json()
        assert r1["scanId"]
        assert r1["scannedAt"]
        assert r1["window"] == {"start": 0.0, "end": 30.0}
        assert r1["summary"]["total"] == 3

        rw = c.post(f"/api/projects/{pid}/quality-check",
                    json={"start": 0, "end": 30}).json()
        r2 = c.post(f"/api/projects/{pid}/quality-check",
                    json={"start": 0, "end": 10}).json()
        assert r2["window"] == {"start": 0.0, "end": 10.0}
        r3 = c.post(f"/api/projects/{pid}/quality-check",
                    json={"start": 15, "end": 30}).json()

        # list endpoint: newest first, project-scoped, summaries only
        scans = c.get(f"/api/projects/{pid}/quality-scans").json()
        assert [s["id"] for s in scans[:4]] == [
            r3["scanId"], r2["scanId"], rw["scanId"], r1["scanId"]]
        assert all("issues" not in s for s in scans)
        s0 = scans[0]
        assert s0["window"] == {"start": 15.0, "end": 30.0}
        assert s0["explicitWindow"] is True
        assert s0["summary"]["total"] == 2
        assert s0["createdAt"]
        # whole-document scan records an implicit window
        assert next(s for s in scans
                    if s["id"] == r1["scanId"])["explicitWindow"] is False
        assert next(s for s in scans
                    if s["id"] == rw["scanId"])["explicitWindow"] is True
        # other project's scans are not mixed in
        assert len(c.get(
            f"/api/projects/{p2['id']}/quality-scans").json()) == 1

        # detail endpoint carries the full issue snapshot
        detail = c.get(
            f"/api/projects/{pid}/quality-scans/{r1['scanId']}").json()
        assert {q["issueId"] for q in detail["issues"]} == \
            {OOB_ID, OV_ID, SP_ID}
        assert detail["summary"]["outOfBounds"] == 1
        assert c.get(
            f"/api/projects/{pid}/quality-scans/nope").status_code == 404
        # a scan from another project is not readable under this project
        other_scan = c.get(
            f"/api/projects/{p2['id']}/quality-scans").json()[0]
        assert c.get(
            f"/api/projects/{pid}/quality-scans/{other_scan['id']}"
        ).status_code == 404

        # --- compare two scans of the SAME window ------------------------
        # fix the out-of-bounds annotation, then scan the same whole window
        fixed_doc = {
            "segments": DOC["segments"],
            "annotations": [
                ({**a, "start": 18, "end": 20} if a["id"] == "ann-oob"
                 else a)
                for a in DOC["annotations"]
            ],
        }
        ver = c.put(f"/api/projects/{pid}",
                    json={"name": "历史访谈", "document": fixed_doc,
                          "documentVersion": ver}
                    ).json()["documentVersion"]
        # explicit fixed window (0-30) keeps the window identical even though
        # the document's own extent shrank to 0-20 after the trim
        r4 = c.post(f"/api/projects/{pid}/quality-check",
                    json={"start": 0, "end": 30}).json()
        assert r4["window"] == {"start": 0.0, "end": 30.0}

        # argument order is irrelevant: older scan is always the base
        for body in ({"scanIdA": rw["scanId"], "scanIdB": r4["scanId"]},
                     {"scanIdA": r4["scanId"], "scanIdB": rw["scanId"]}):
            d = c.post(f"/api/projects/{pid}/quality-scans/compare",
                       json=body)
            assert d.status_code == 200, d.text
            cmp = d.json()
            assert cmp["window"] == {"start": 0.0, "end": 30.0}
            assert cmp["base"]["id"] == rw["scanId"]
            assert cmp["target"]["id"] == r4["scanId"]
            assert cmp["counts"] == \
                {"new": 0, "resolved": 1, "persistent": 2}
            assert [q["issueId"] for q in cmp["resolvedIssues"]] == [OOB_ID]
            assert {q["issueId"] for q in cmp["persistentIssues"]} == \
                {OV_ID, SP_ID}
            assert cmp["newIssues"] == []

        # introduce a NEW issue (new overlap) in the same explicit window
        new_doc = {
            "segments": fixed_doc["segments"],
            "annotations": fixed_doc["annotations"] + [
                {"id": "ann-c", "start": 1, "end": 3,
                 "speaker": "访谈者", "topic": "重叠丙"},
            ],
        }
        c.put(f"/api/projects/{pid}",
              json={"name": "历史访谈", "document": new_doc,
                    "documentVersion": ver})
        r5 = c.post(f"/api/projects/{pid}/quality-check",
                    json={"start": 0, "end": 30}).json()
        cmp2 = c.post(f"/api/projects/{pid}/quality-scans/compare",
                      json={"scanIdA": rw["scanId"],
                            "scanIdB": r5["scanId"]}).json()
        assert cmp2["counts"]["new"] == 2
        assert cmp2["counts"]["resolved"] == 1
        assert cmp2["counts"]["persistent"] == 2
        new_ids = {q["issueId"] for q in cmp2["newIssues"]}
        assert new_ids == {"qc:overlap:ann-a|ann-c",
                           "qc:overlap:ann-b|ann-c"}

        # re-scanning the whole (now shorter) document yields a *different*
        # implicit window than the original whole-document scan -> forbidden
        r4_default = c.post(f"/api/projects/{pid}/quality-check").json()
        assert r4_default["window"] == {"start": 0.0, "end": 20.0}
        forbid = c.post(f"/api/projects/{pid}/quality-scans/compare",
                        json={"scanIdA": r1["scanId"],
                              "scanIdB": r4_default["scanId"]})
        assert forbid.status_code == 400
        assert "时间窗口" in forbid.json()["detail"]

        # --- comparing scans with DIFFERENT windows is forbidden ---------
        bad = c.post(f"/api/projects/{pid}/quality-scans/compare",
                     json={"scanIdA": r1["scanId"], "scanIdB": r2["scanId"]})
        assert bad.status_code == 400, bad.text
        assert "时间窗口" in bad.json()["detail"]
        assert r2["window"] != r3["window"]
        bad2 = c.post(f"/api/projects/{pid}/quality-scans/compare",
                      json={"scanIdA": r2["scanId"], "scanIdB": r3["scanId"]})
        assert bad2.status_code == 400

        # same scan twice / missing scans
        same = c.post(f"/api/projects/{pid}/quality-scans/compare",
                      json={"scanIdA": r1["scanId"], "scanIdB": r1["scanId"]})
        assert same.status_code == 400
        missing = c.post(f"/api/projects/{pid}/quality-scans/compare",
                         json={"scanIdA": r1["scanId"], "scanIdB": "scan-x"})
        assert missing.status_code == 404
        assert c.post(
            "/api/projects/nope/quality-scans/compare",
            json={"scanIdA": "a", "scanIdB": "b"}).status_code == 404

        # history endpoints require the project to exist
        assert c.get(
            f"/api/projects/{pid}/quality-scans").status_code == 200
        assert c.post(
            f"/api/projects/nope/quality-check").status_code == 404

        # --- deleting the project wipes its scan history (cascade) -------
        assert c.delete(f"/api/projects/{pid}").status_code == 204
        assert c.get(
            f"/api/projects/{pid}/quality-scans").status_code == 404
        assert c.get(
            f"/api/projects/{pid}/quality-scans/{r1['scanId']}"
        ).status_code == 404
        # the other project's history is untouched
        assert len(c.get(
            f"/api/projects/{p2['id']}/quality-scans").json()) == 1

        print("ALL QUALITY SCAN HISTORY TESTS PASSED")


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
