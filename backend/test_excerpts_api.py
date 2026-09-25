"""摘录包（excerpt package）引擎与端到端 API 测试。

Run with: python3 test_excerpts_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="excerpt-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.excerpts import build_excerpt, normalize_ranges  # noqa: E402

# Intervals used below: A=[0,9], B=[10,30] (gap (9,10) between them),
# C=[60,90].
#
#   s1 [0,10]    contained in A? [0,10) vs [0,9): end 10 > 9 -> crosses_end
#   s2 [5,9]     contained in A
#   s3 [8,22]    bridges the A-B gap: crosses A's end and B's start
#   s4 [22,30]   contained in B
#   s5 [40,70]   crosses C's start (no contact with B which ends at 30)
#   s6 [60,65]   contained in C
#   s7 [88,95]   crosses C's end
DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 10, "speaker": "甲", "text": "探入空隙"},
        {"id": "s2", "start": 5, "end": 9, "speaker": "甲", "text": "完整一"},
        {"id": "s3", "start": 8, "end": 22, "speaker": "甲", "text": "跨界前段"},
        {"id": "s4", "start": 22, "end": 30, "speaker": "乙", "text": "完整二"},
        {"id": "s5", "start": 40, "end": 70, "speaker": "乙", "text": "跨界C前段"},
        {"id": "s6", "start": 60, "end": 65, "speaker": "丙", "text": "完整三"},
        {"id": "s7", "start": 88, "end": 95, "speaker": "丙", "text": "跨界后段"},
    ],
    "annotations": [
        # spans B and C (with a gap) -> split into two clips
        {"id": "ann-1", "start": 20, "end": 80, "speaker": "甲",
         "topic": "跨区间校注", "note": "原样保留"},
        # fully inside B
        {"id": "ann-2", "start": 22, "end": 30, "speaker": "乙",
         "topic": "正好一个片段"},
        # only touches C's start at 60 -> half-open: no intersection
        {"id": "ann-3", "start": 55, "end": 60, "topic": "端点相接"},
        # starts in the A-B gap and ends inside B -> clipped to B's start;
        # only touches A at 9, so it must NOT appear in A
        {"id": "ann-4", "start": 9, "end": 25, "topic": "越入B"},
    ],
}


def test_engine():
    res = build_excerpt(DOC, [
        {"start": 60, "end": 90},  # C, entered first & out of order
        {"start": 10, "end": 20},  # touching pair -> merges into B [10,30]
        {"start": 20, "end": 30},
        {"start": 0, "end": 9},    # A
    ])
    ivs = res["intervals"]
    assert [(iv["start"], iv["end"]) for iv in ivs] == \
        [(0.0, 9.0), (10.0, 30.0), (60.0, 90.0)]
    a, b, c = ivs

    # the touching input pair was merged, sources preserved
    assert len(b["sources"]) == 2

    # segments: only fully contained ones are included. s3 [8,22) crosses
    # both A's end and B's start, so it is excluded from both — its full
    # text must never be quoted as an excerpt.
    assert [s["id"] for s in a["segments"]] == ["s2"]
    assert [s["id"] for s in b["segments"]] == ["s4"]
    assert [s["id"] for s in c["segments"]] == ["s6"]

    a_excl = {x["id"]: x["reason"] for x in a["excludedSegments"]}
    b_excl = {x["id"]: x["reason"] for x in b["excludedSegments"]}
    c_excl = {x["id"]: x["reason"] for x in c["excludedSegments"]}
    assert a_excl == {"s1": "crosses_end", "s3": "crosses_end"}
    assert b_excl == {"s3": "crosses_start"}
    assert c_excl == {"s5": "crosses_start", "s7": "crosses_end"}

    # annotations of B: ann-1 clipped [20,30] as part 1, ann-2 kept whole,
    # ann-4 clipped at B's start -> [10,25]
    b_anns = {x["sourceAnnotationId"]: x for x in b["annotations"]}
    assert set(b_anns) == {"ann-1", "ann-2", "ann-4"}
    assert (b_anns["ann-1"]["start"], b_anns["ann-1"]["end"]) == (20.0, 30.0)
    assert b_anns["ann-1"]["sourcePart"] == 1
    assert b_anns["ann-1"]["sourcePartCount"] == 2
    assert b_anns["ann-1"]["id"] == "exc-ann-1-1"
    assert b_anns["ann-1"]["topic"] == "跨区间校注"
    assert b_anns["ann-1"]["note"] == "原样保留"
    assert (b_anns["ann-4"]["start"], b_anns["ann-4"]["end"]) == (10.0, 25.0)
    assert "sourcePart" not in b_anns["ann-4"]

    # A only touches ann-4 at 9 (half-open) -> no annotations on A
    assert a["annotations"] == []

    # C: ann-1 clipped [60,80] as part 2; ann-3 only touches at 60 -> absent
    c_anns = {x["sourceAnnotationId"]: x for x in c["annotations"]}
    assert set(c_anns) == {"ann-1"}
    assert (c_anns["ann-1"]["start"], c_anns["ann-1"]["end"]) == (60.0, 80.0)
    assert c_anns["ann-1"]["sourcePart"] == 2
    assert c_anns["ann-1"]["id"] == "exc-ann-1-2"

    # global exclusion list is deduplicated with interval indices. s3
    # bridges the gap between A and B -> one gap-crossing entry referencing
    # both intervals.
    g = {x["id"]: x for x in res["excludedSegments"]}
    assert set(g) == {"s1", "s3", "s5", "s7"}
    assert g["s3"]["intervalIndices"] == [0, 1]
    assert g["s3"]["reason"] == "crosses_gap"
    assert g["s1"]["intervalIndices"] == [0]
    assert g["s1"]["reason"] == "crosses_end"
    assert g["s5"]["reason"] == "crosses_start"
    assert g["s7"]["reason"] == "crosses_end"
    # excluded segments keep their full original text (no partial quoting)
    assert g["s3"]["text"] == "跨界前段"

    assert res["summary"] == {
        "inputRangeCount": 4,
        "mergedRangeCount": 3,
        "segmentCount": 3,       # s2 (A), s4 (B), s6 (C)
        "annotationCount": 4,
        "excludedSegmentCount": 4,
        "splitAnnotationCount": 1,
    }


def test_segment_bridging_two_intervals():
    """A segment intersecting two disjoint intervals without being contained
    in either is excluded once, referencing every interval it touches, and
    flagged as gap-crossing."""
    doc = {"segments": [
        {"id": "x", "start": 5, "end": 40, "text": "跨过空隙的长片段"},
        {"id": "ok", "start": 10, "end": 12, "text": "完整"},
    ], "annotations": []}
    res = build_excerpt(doc, [{"start": 0, "end": 15}, {"start": 35, "end": 50}])
    assert len(res["intervals"]) == 2
    x = next(e for e in res["excludedSegments"] if e["id"] == "x")
    assert x["intervalIndices"] == [0, 1]
    assert x["reason"] == "crosses_gap"
    assert res["intervals"][0]["segments"][0]["id"] == "ok"
    assert res["intervals"][1]["segments"] == []


def test_touching_inputs_merge_and_both_sided_cross():
    doc = {"segments": [
        {"id": "mid", "start": 10, "end": 20, "text": "完整"},
        {"id": "big", "start": 2, "end": 30, "text": "两侧都伸出"},
    ], "annotations": []}
    res = build_excerpt(doc, [{"start": 5, "end": 15}, {"start": 15, "end": 25}])
    assert len(res["intervals"]) == 1
    iv = res["intervals"][0]
    assert (iv["start"], iv["end"]) == (5.0, 25.0)
    assert [s["id"] for s in iv["segments"]] == ["mid"]
    assert iv["excludedSegments"][0]["reason"] == "crosses_both"


def test_invalid_ranges():
    doc = {"segments": [], "annotations": []}
    for bad in ([], [{"start": 10, "end": 10}], [{"start": -1, "end": 5}],
                [{"start": 9, "end": 5}], "not-a-list", [{"start": 1}]):
        try:
            normalize_ranges(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass
    # engine-level guard as well
    try:
        build_excerpt(doc, [])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_api():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "摘录包访谈"}).json()
        pid = p["id"]
        c.put(f"/api/projects/{pid}",
              json={"name": "摘录包访谈", "document": DOC,
                    "documentVersion": p["documentVersion"]})

        body = {"ranges": [
            {"start": 60, "end": 90},
            {"start": 10, "end": 20},
            {"start": 20, "end": 30},
            {"start": 0, "end": 9},
        ]}
        r = c.post(f"/api/projects/{pid}/excerpts/preview", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert data["projectName"] == "摘录包访谈"
        assert [(iv["start"], iv["end"]) for iv in data["intervals"]] == \
            [(0.0, 9.0), (10.0, 30.0), (60.0, 90.0)]
        assert data["summary"]["splitAnnotationCount"] == 1

        # split annotation keeps the original id as source marker
        clips = [(a["sourceAnnotationId"], a["start"], a["end"],
                  a.get("sourcePart"))
                 for iv in data["intervals"] for a in iv["annotations"]
                 if a["sourceAnnotationId"] == "ann-1"]
        assert sorted(clips) == sorted([
            ("ann-1", 20.0, 30.0, 1),
            ("ann-1", 60.0, 80.0, 2),
        ])

        # validation: empty / bad ranges -> 400; negative -> 422 (pydantic)
        assert c.post(f"/api/projects/{pid}/excerpts/preview",
                      json={"ranges": []}).status_code == 422
        assert c.post(f"/api/projects/{pid}/excerpts/preview",
                      json={"ranges": [{"start": 9, "end": 5}]}
                      ).status_code == 400
        assert c.post(f"/api/projects/{pid}/excerpts/preview",
                      json={"ranges": [{"start": -1, "end": 5}]}
                      ).status_code == 422

        # unknown project -> 404
        assert c.post("/api/projects/nope/excerpts/preview",
                      json=body).status_code == 404

        # download returns an attachment with the same payload; the project
        # document must be unchanged afterwards.
        r = c.post(f"/api/projects/{pid}/excerpts/download", json=body)
        assert r.status_code == 200, r.text
        cd = r.headers["content-disposition"]
        assert "attachment" in cd and "%E6%91%98%E5%BD%95%E5%8C%85" in cd
        pkg = r.json()
        assert pkg["summary"] == data["summary"]
        assert [iv["start"] for iv in pkg["intervals"]] == [0.0, 10.0, 60.0]
        # the package carries interval ranges, full segments and clipped
        # annotations with source markers
        first_iv = pkg["intervals"][0]
        assert {"start", "end", "sources", "segments", "annotations",
                "excludedSegments"} <= set(first_iv)
        clip = next(a for iv in pkg["intervals"] for a in iv["annotations"]
                    if a["sourceAnnotationId"] == "ann-1"
                    and a.get("sourcePart") == 1)
        assert clip["note"] == "原样保留"
        after = c.get(f"/api/projects/{pid}").json()
        assert after["document"] == DOC

        print("ALL EXCERPT API TESTS PASSED")


def run():
    test_engine()
    test_segment_bridging_two_intervals()
    test_touching_inputs_merge_and_both_sided_cross()
    test_invalid_ranges()
    test_api()


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
