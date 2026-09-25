"""End-to-end API tests for the annotation quality-check workbench.

Run with: python3 test_quality_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="quality-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

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
        # past transcript end, speaker correct -> only out_of_bounds
        {"id": "ann-oob", "start": 18, "end": 30,
         "speaker": "受访者", "topic": "越界条"},
        # containment overlap inside one-speaker region -> only overlap
        {"id": "ann-a", "start": 0, "end": 5,
         "speaker": "访谈者", "topic": "重叠甲"},
        {"id": "ann-b", "start": 1, "end": 4,
         "speaker": "访谈者", "topic": "重叠乙"},
        # wrong speaker, isolated region -> only speaker_mismatch
        {"id": "ann-sp", "start": 15, "end": 18,
         "speaker": "访谈者", "topic": "说错话人"},
    ],
}


def run():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "质检访谈"}).json()
        pid = p["id"]
        c.put(f"/api/projects/{pid}",
              json={"name": "质检访谈", "document": DOC})

        # default (no body): whole document
        r = c.post(f"/api/projects/{pid}/quality-check")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert data["projectName"] == "质检访谈"
        assert data["window"] == {"start": 0.0, "end": 30.0}
        assert data["summary"]["total"] == 3, data["summary"]
        assert data["summary"]["error"] == 3
        assert data["summary"]["warning"] == 0
        assert data["summary"]["info"] == 0
        assert data["summary"]["outOfBounds"] == 1
        assert data["summary"]["overlap"] == 1
        assert data["summary"]["speakerMismatch"] == 1
        assert data["summary"]["speakerMissing"] == 0
        assert data["summary"]["fixable"] == 1
        assert data["summary"]["scanLines"] > 0
        ids = [q["issueId"] for q in data["issues"]]
        assert ids == [
            "qc:overlap:ann-a|ann-b",
            "qc:speaker_mismatch:ann-sp",
            "qc:out_of_bounds:ann-oob",
        ]
        # errors are sorted by start time
        assert [q["start"] for q in data["issues"]] == [1.0, 15.0, 18.0]

        oob = next(q for q in data["issues"]
                   if q["type"] == "out_of_bounds")
        assert oob["severity"] == "error"
        assert oob["fixable"] is True
        assert oob["fix"] == {"start": 18.0, "end": 20.0}
        assert oob["evidence"]["uncoveredRanges"] == [
            {"start": 20.0, "end": 30.0}]
        assert oob["evidence"]["transcriptCoverage"] == \
            {"start": 0.0, "end": 20.0}
        assert oob["annotations"][0]["id"] == "ann-oob"
        assert oob["annotationIds"] == ["ann-oob"]
        assert oob["start"] == 18.0 and oob["end"] == 30.0

        ov = next(q for q in data["issues"] if q["type"] == "overlap")
        assert ov["evidence"]["overlapSeconds"] == 3.0
        assert ov["evidence"]["ranges"] == [{"start": 1.0, "end": 4.0}]
        assert ov["annotationIds"] == ["ann-a", "ann-b"]
        assert ov["fixable"] is False
        # evidence annotations carry topic/speaker for display
        assert {a["topic"] for a in ov["annotations"]} == \
            {"重叠甲", "重叠乙"}

        sp = next(q for q in data["issues"]
                  if q["type"] == "speaker_mismatch")
        assert sp["evidence"]["annotationSpeaker"] == "访谈者"
        assert sp["evidence"]["segmentSpeakers"] == ["受访者"]
        assert sp["evidence"]["segments"][0]["id"] == "s3"
        assert sp["evidence"]["segments"][0]["coveredSeconds"] == 3.0

        # empty JSON object behaves like the default
        r2 = c.post(f"/api/projects/{pid}/quality-check", json={})
        assert r2.status_code == 200
        assert r2.json()["summary"]["total"] == 3

        # time window: restrict to the overlap region only
        rw = c.post(f"/api/projects/{pid}/quality-check",
                    json={"start": 0, "end": 10})
        dw = rw.json()
        assert dw["window"] == {"start": 0.0, "end": 10.0}
        assert {q["issueId"] for q in dw["issues"]} == \
            {"qc:overlap:ann-a|ann-b"}
        assert dw["summary"]["fixable"] == 0

        # window covering only the speaker issue (stops before ann-oob at 18)
        rw2 = c.post(f"/api/projects/{pid}/quality-check",
                     json={"start": 15, "end": 17.9})
        assert {q["issueId"] for q in rw2.json()["issues"]} == \
            {"qc:speaker_mismatch:ann-sp"}

        # invalid window -> 400/422
        bad = c.post(f"/api/projects/{pid}/quality-check",
                     json={"start": 20, "end": 5})
        assert bad.status_code == 400, bad.text
        bad2 = c.post(f"/api/projects/{pid}/quality-check",
                      json={"start": -1})
        assert bad2.status_code == 422, bad2.text

        # one-click clip flow: apply the reported fix, save, re-check
        fixed = oob["fix"]
        updated = {
            "segments": DOC["segments"],
            "annotations": [
                ({**a, **fixed} if a["id"] == "ann-oob" else a)
                for a in DOC["annotations"]
            ],
        }
        save = c.put(f"/api/projects/{pid}",
                     json={"name": "质检访谈", "document": updated})
        assert save.status_code == 200
        again = c.post(f"/api/projects/{pid}/quality-check").json()
        assert not any(q["issueId"] == "qc:out_of_bounds:ann-oob"
                       for q in again["issues"])
        assert again["summary"]["outOfBounds"] == 0
        assert again["summary"]["fixable"] == 0
        assert again["summary"]["total"] == 2  # overlap + mismatch remain

        # unknown project -> 404
        assert c.post("/api/projects/nope/quality-check").status_code == 404

        print("ALL QUALITY API TESTS PASSED")


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
