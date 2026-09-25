"""校注覆盖盲区（coverage gaps）引擎与端到端 API 测试。

Run with: python3 test_coverage_api.py  (or via pytest).
"""
import os
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="coverage-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.coverage import coverage_report  # noqa: E402
from app.main import app  # noqa: E402

# Segment / annotation layout:
#
#   s1 [0,10]  甲          ann a1 [2,8]   speaker 甲  -> covers 6s
#   s2 [10,20] 甲 (touches s1, merges to [0,20))
#                            ann a2 [18,25] speaker 甲 -> covers 2s
#                                                    (20-25 beyond speech)
#   s3 [30,40] 乙          ann a3 [32,36] speaker 甲 -> wrong speaker: 0
#   s4 [40,45] 乙 (touches s3, merges [30,45))
#                            ann a4 [35,38] speaker 乙 -> covers 3s
#   s5 [50,60] 甲 乙? no — keep separate
#   s5 [50,60] 甲          ann a5 [0,100]  speaker 甲 -> covers 10s only
#   s6 [55,65] 乙 (overlaps s5)            (multi-speaker counted separately)
#   s7 [70,70] 丙 zero length -> ignored
#   s8 [80] bad end type -> ignored
DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 10, "speaker": "甲", "text": "a"},
        {"id": "s2", "start": 10, "end": 20, "speaker": "甲", "text": "b"},
        {"id": "s3", "start": 30, "end": 40, "speaker": "乙", "text": "c"},
        {"id": "s4", "start": 40, "end": 45, "speaker": "乙", "text": "d"},
        {"id": "s5", "start": 50, "end": 60, "speaker": "甲", "text": "e"},
        {"id": "s6", "start": 55, "end": 65, "speaker": "乙", "text": "f"},
        {"id": "s7", "start": 70, "end": 70, "speaker": "丙", "text": "x"},
        {"id": "s8", "start": 80, "end": "nope", "speaker": "丙", "text": "y"},
        {"id": "s9", "start": 90, "end": 95, "speaker": "  ", "text": "z"},
    ],
    "annotations": [
        {"id": "a1", "start": 2, "end": 8, "speaker": "甲", "topic": "t1"},
        {"id": "a2", "start": 18, "end": 25, "speaker": "甲", "topic": "t2"},
        {"id": "a3", "start": 32, "end": 36, "speaker": "甲", "topic": "wrong"},
        {"id": "a4", "start": 35, "end": 38, "speaker": "乙", "topic": "t4"},
        {"id": "a5", "start": 0, "end": 100, "speaker": "甲", "topic": "big"},
        {"id": "a6", "start": 0, "end": 0, "speaker": "甲"},
        {"id": "a7", "start": 100, "end": 110, "speaker": "   "},
    ],
}


def _by_speaker(result):
    return {sp["speaker"]: sp for sp in result["speakers"]}


def test_merge_touching_and_overlapping_segments_per_speaker():
    res = coverage_report({"segments": DOC["segments"], "annotations": []})
    sp = _by_speaker(res)
    # 甲: [0,20) + [50,60) = 30s ; 乙: [30,45) + [55,65) = 25s
    assert sp["甲"]["speechMs"] == 30_000
    assert sp["乙"]["speechMs"] == 25_000
    assert "丙" not in sp  # zero-length / invalid segments ignored
    assert res["summary"]["speechMs"] == 55_000
    assert sp["甲"]["speechRanges"] == [
        {"start": 0.0, "end": 20.0}, {"start": 50.0, "end": 60.0}]


def test_only_same_speaker_annotations_cover():
    res = coverage_report(DOC)
    sp = _by_speaker(res)
    # a3 belongs to 甲: it never covers 乙's speech
    yi = sp["乙"]
    # 乙 speech [30,45) + [55,65); covered only by a4 [35,38) = 3s
    assert yi["coveredMs"] == 3_000
    assert yi["uncoveredMs"] == 22_000
    assert yi["coverage"] == 0.12


def test_annotation_beyond_speech_not_counted_and_overlaps_unioned():
    res = coverage_report(DOC)
    jia = _by_speaker(res)["甲"]
    # a5 [0,100) clips speech fully: [0,20) and [50,60) all covered;
    # a1/a2 overlap a5 and must not double-count; a2's 20-25 is dropped.
    assert jia["coveredMs"] == 30_000
    assert jia["uncoveredMs"] == 0
    assert jia["coverage"] == 1.0
    assert jia["uncoveredRanges"] == []
    assert jia["uncoveredCount"] == 0


def test_uncovered_gap_ranges_half_open():
    doc = {
        "segments": [
            {"id": "s1", "start": 0, "end": 10, "speaker": "甲"},
            {"id": "s2", "start": 20, "end": 30, "speaker": "甲"},
        ],
        "annotations": [
            {"id": "a1", "start": 2, "end": 6, "speaker": "甲"},
            # touching coverage endpoint: [6,?) is not covered twice
            {"id": "a2", "start": 6, "end": 8, "speaker": "甲"},
            {"id": "a3", "start": 22, "end": 24, "speaker": "甲"},
        ],
    }
    res = coverage_report(doc)
    sp = _by_speaker(res)["甲"]
    # covered [2,8) = 6s on first piece + [22,24) = 2s on second
    assert sp["coveredMs"] == 8_000
    assert sp["speechMs"] == 20_000
    assert [(g["start"], g["end"]) for g in sp["uncoveredRanges"]] == [
        (0.0, 2.0), (8.0, 10.0), (20.0, 22.0), (24.0, 30.0)]
    assert sp["uncoveredCount"] == 4
    assert sp["uncoveredRanges"][0]["duration"] == 2.0


def test_multi_speaker_speech_counted_independently():
    doc = {
        # 甲 and 乙 both speak [10,20); only 甲 annotates it
        "segments": [
            {"id": "s1", "start": 10, "end": 20, "speaker": "甲"},
            {"id": "s2", "start": 10, "end": 20, "speaker": "乙"},
        ],
        "annotations": [
            {"id": "a1", "start": 10, "end": 20, "speaker": "甲"},
        ],
    }
    res = coverage_report(doc)
    sp = _by_speaker(res)
    assert sp["甲"]["coveredMs"] == 10_000
    assert sp["甲"]["coverage"] == 1.0
    assert sp["乙"]["coveredMs"] == 0
    assert sp["乙"]["coverage"] == 0.0
    # each speaker's own 10s counts independently (20s total)
    assert res["summary"]["speechMs"] == 20_000
    assert res["summary"]["coveredMs"] == 10_000
    assert res["summary"]["coverage"] == 0.5


def test_millisecond_rounding_matches_frontend():
    # 2.675 s -> 2675 ms (round half up, like JS Math.round)
    doc = {
        "segments": [{"id": "s1", "start": 0, "end": 2.675,
                      "speaker": "甲"}],
        "annotations": [],
    }
    sp = coverage_report(doc)["speakers"][0]
    assert sp["speechMs"] == 2675
    assert sp["speechSeconds"] == 2.675


def test_speaker_filter_and_unknown_name():
    res = coverage_report(DOC, speaker=" 乙 ")
    assert [sp["speaker"] for sp in res["speakers"]] == ["乙"]
    res_none = coverage_report(DOC, speaker="不存在的人")
    assert res_none["speakers"] == []
    assert res_none["summary"]["speakers"] == 0
    assert res_none["summary"]["speechMs"] == 0
    assert res_none["summary"]["coverage"] == 0.0


def test_empty_and_invalid_documents():
    assert coverage_report(None)["speakers"] == []
    assert coverage_report({})["summary"]["coverage"] == 0.0
    doc = {"segments": [{"id": "x", "start": 10, "end": 5,
                         "speaker": "甲"}]}
    assert coverage_report(doc)["speakers"] == []


def test_speaker_order_follows_first_segment_appearance():
    doc = {
        "segments": [
            {"id": "s1", "start": 0, "end": 1, "speaker": "乙"},
            {"id": "s2", "start": 1, "end": 2, "speaker": "甲"},
        ],
        "annotations": [
            {"id": "a1", "start": 0, "end": 1, "speaker": "甲"},
            {"id": "a2", "start": 1, "end": 2, "speaker": "乙"},
        ],
    }
    assert [sp["speaker"] for sp in coverage_report(doc)["speakers"]] == \
        ["乙", "甲"]


# ---------------- end-to-end API ----------------

# use as a context manager so the startup hook initializes SQLite
client = None


def _client():
    global client
    if client is None:
        client = TestClient(app).__enter__()
    return client


def _create(document):
    c = _client()
    res = c.post("/api/projects", json={"name": "覆盖测试"})
    assert res.status_code == 201
    created = res.json()
    pid = created["id"]
    res = c.put(f"/api/projects/{pid}",
                json={"document": document,
                      "documentVersion": created["documentVersion"]})
    assert res.status_code == 200
    return pid


def test_coverage_api_full_report():
    c = _client()
    pid = _create(DOC)
    res = c.get(f"/api/projects/{pid}/coverage")
    assert res.status_code == 200
    data = res.json()
    assert data["projectId"] == pid
    assert data["projectName"] == "覆盖测试"
    assert data["speaker"] is None
    assert data["summary"]["speakers"] == 2
    assert data["summary"]["speechMs"] == 55_000
    sp = {x["speaker"]: x for x in data["speakers"]}
    assert sp["甲"]["coverage"] == 1.0
    assert sp["乙"]["uncoveredCount"] == 3
    # 乙 speech [30,45),[55,65); covered only [35,38); gaps are
    # [30,35), [38,45), [55,65) (the 45-55 silence is no one's speech)
    assert [(g["start"], g["end"]) for g in sp["乙"]["uncoveredRanges"]] == [
        (30.0, 35.0), (38.0, 45.0), (55.0, 65.0)]


def test_coverage_api_speaker_filter():
    c = _client()
    pid = _create(DOC)
    res = c.get(f"/api/projects/{pid}/coverage",
                params={"speaker": "乙"})
    assert res.status_code == 200
    data = res.json()
    assert data["speaker"] == "乙"
    assert [x["speaker"] for x in data["speakers"]] == ["乙"]
    # unknown speaker is a valid empty report, not 404
    res2 = c.get(f"/api/projects/{pid}/coverage",
                 params={"speaker": "无名氏"})
    assert res2.status_code == 200
    assert res2.json()["speakers"] == []


def test_coverage_api_unknown_project_404():
    c = _client()
    res = c.get("/api/projects/proj-does-not-exist/coverage")
    assert res.status_code == 404
