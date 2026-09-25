"""公开版转写（public transcript）引擎与端到端 API 测试。

Run with: python3 test_public_transcript_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="public-transcript-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.redact import build_public_transcript, public_document  # noqa: E402

# Segment layout used by the engine tests:
#
#   s1 [0,4]   甲 "后来 2003 年"   ─┐ touching, same speaker -> one chain
#   s2 [4,8]   甲 "改制 方案"      ─┘
#   s3 [10,14] 甲 "后来改制"        gap (8 -> 10) breaks the chain
#   s4 [13,16] 甲 "改制"            overlap (13 < 14) breaks the chain
#   s5 [16,20] 乙 "后来改制"        speaker switch breaks the chain ─┐ touching
#   s6 [20,24] 乙 "再谈改制"                                       ─┘ one chain
#   s7 [30,25] 丙 "后来"            invalid times -> singleton chain
DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 4, "speaker": "甲",
         "text": "后来 2003 年"},
        {"id": "s2", "start": 4, "end": 8, "speaker": "甲",
         "text": "改制 方案"},
        {"id": "s3", "start": 10, "end": 14, "speaker": "甲", "text": "后来改制"},
        {"id": "s4", "start": 13, "end": 16, "speaker": "甲", "text": "改制"},
        {"id": "s5", "start": 16, "end": 20, "speaker": "乙", "text": "后来改制"},
        {"id": "s6", "start": 20, "end": 24, "speaker": "乙", "text": "再谈改制"},
        {"id": "s7", "start": 30, "end": 25, "speaker": "丙", "text": "后来"},
    ],
    "annotations": [
        {"id": "ann-1", "start": 0, "end": 8, "speaker": "甲",
         "topic": "校注不进入公开版"},
    ],
}


def _by_id(result):
    return {s["id"]: s for s in result["segments"]}


def test_cross_segment_hit_ignores_whitespace():
    """A hit may span time-touching same-speaker segments; whitespace in the
    text is skipped for matching but preserved in the masked output."""
    res = build_public_transcript(DOC, ["年改制"], [], False)
    segs = _by_id(res)
    # chain text is "后来2003年改制方案": "年改制" hits 年(s1) + 改制(s2)
    assert segs["s1"]["text"] == "后来 2003 ■"
    assert segs["s1"]["maskedRanges"] == [[8, 9]]
    assert segs["s2"]["text"] == "■■ 方案"  # original blank kept
    assert segs["s2"]["maskedRanges"] == [[0, 2]]
    # the query's own whitespace is ignored as well
    res2 = build_public_transcript(DOC, ["年 改 制"], [], False)
    assert _by_id(res2)["s2"]["text"] == "■■ 方案"
    assert res["summary"]["hitCount"] == 1
    assert res["summary"]["maskedCharCount"] == 3
    assert res["summary"]["maskedSegmentCount"] == 2


def test_gap_overlap_and_speaker_switch_break_chains():
    res = build_public_transcript(DOC, ["后来改制"], [], False)
    segs = _by_id(res)
    # s1/s2 chain: stripped text "后来2003年改制方案" contains no "后来改制"
    assert segs["s1"]["text"] == "后来 2003 年"
    assert segs["s2"]["text"] == "改制 方案"
    # s3 is a singleton (gap before it): the word fully inside still masks
    assert segs["s3"]["text"] == "■■■■"
    # s4 is a singleton (overlap with s3): "后来改制" does not fit -> intact
    assert segs["s4"]["text"] == "改制"
    # s5/s6 chain (speaker 乙): hit at the start of the chain
    assert segs["s5"]["text"] == "■■■■"
    assert segs["s6"]["text"] == "再谈改制"
    # invalid-time segment forms its own chain: "后来改制" cannot fit
    assert segs["s7"]["text"] == "后来"


def test_overlapping_hits_mask_only_once():
    """Different words hitting the same characters mask them once."""
    res = build_public_transcript(DOC, ["后来改制", "改制"], [], False)
    segs = _by_id(res)
    # s5: "后来改制" covers all 4 chars; "改制" re-hits chars 2-3 -> union 4
    assert segs["s5"]["text"] == "■■■■"
    assert segs["s5"]["maskedChars"] == 4
    assert segs["s5"]["maskedRanges"] == [[0, 4]]
    # s6: only "改制" hits (positions 2-3)
    assert segs["s6"]["text"] == "再谈■■"
    assert segs["s6"]["maskedChars"] == 2
    # occurrences are counted per hit, characters per union
    words = {w["word"]: w["count"] for w in res["words"]}
    # "后来改制": s3, s5 -> 2;  "改制": s2, s3, s4, s5, s6 -> 5
    assert words == {"后来改制": 2, "改制": 5}
    assert res["summary"]["hitCount"] == 7
    # masked chars: s2(2) + s3(4) + s4(2) + s5(4) + s6(2) = 14, each once
    assert res["summary"]["maskedCharCount"] == 14


def test_case_sensitivity():
    doc = {"segments": [
        {"id": "a", "start": 0, "end": 2, "speaker": "甲", "text": "Ab 公司"},
    ], "annotations": []}
    res = build_public_transcript(doc, ["ab"], [], False)
    assert res["segments"][0]["text"] == "■■ 公司"
    res_cs = build_public_transcript(doc, ["ab"], [], True)
    assert res_cs["segments"][0]["text"] == "Ab 公司"
    res_cs2 = build_public_transcript(doc, ["Ab"], [], True)
    assert res_cs2["segments"][0]["text"] == "■■ 公司"


def test_speaker_alias_numbering_is_stable():
    """Aliases follow first appearance in the saved transcript; selected but
    absent names take the following numbers in input order."""
    res = build_public_transcript(DOC, [], ["乙", "甲", "丁"], False)
    segs = _by_id(res)
    assert segs["s1"]["speaker"] == "说话人 1"   # 甲 appears first (s1)
    assert segs["s3"]["speaker"] == "说话人 1"
    assert segs["s5"]["speaker"] == "说话人 2"   # 乙 appears later (s5)
    assert segs["s6"]["speaker"] == "说话人 2"
    assert segs["s7"]["speaker"] == "丙"          # not selected -> untouched
    mapping = {sp["original"]: sp for sp in res["speakers"]}
    assert mapping["甲"]["alias"] == "说话人 1"
    assert mapping["甲"]["appears"] is True
    assert mapping["甲"]["segmentCount"] == 4
    assert mapping["乙"]["alias"] == "说话人 2"
    assert mapping["丁"]["alias"] == "说话人 3"   # absent, input order
    assert mapping["丁"]["appears"] is False
    assert res["summary"]["anonymizedSpeakerCount"] == 2
    assert res["summary"]["anonymizedSegmentCount"] == 6
    # same inputs -> identical numbering (preview/download consistency)
    again = build_public_transcript(DOC, [], ["乙", "甲", "丁"], False)
    assert [s["speaker"] for s in again["segments"]] == \
        [s["speaker"] for s in res["segments"]]


def test_segment_identity_and_document_untouched():
    res = build_public_transcript(DOC, ["改制"], ["甲"], False)
    segs = _by_id(res)
    # ids, times and segmentation are preserved one-to-one
    assert [s["id"] for s in res["segments"]] == \
        [s["id"] for s in DOC["segments"]]
    assert (segs["s2"]["start"], segs["s2"]["end"]) == (4, 8)
    # annotations never enter the public transcript
    assert "annotations" not in res
    # the source document was not modified
    assert DOC["segments"][1]["text"] == "改制 方案"
    assert DOC["segments"][0]["speaker"] == "甲"


def test_public_document_is_segments_only():
    res = build_public_transcript(DOC, ["改制"], ["甲"], False)
    res["projectId"] = "proj-x"
    res["projectName"] = "访谈X"
    pub = public_document(res)
    assert pub["kind"] == "public-transcript"
    assert pub["segmentCount"] == len(DOC["segments"])
    assert set(pub) == {"kind", "projectId", "projectName", "generatedAt",
                        "segmentCount", "segments"}
    for seg in pub["segments"]:
        assert set(seg) == {"id", "start", "end", "speaker", "text"}
    # no original speaker names / preview aids leak into the download
    assert "甲" not in str(pub["segments"])
    assert "maskedRanges" not in str(pub)


def test_invalid_inputs():
    doc = {"segments": [], "annotations": []}
    for bad_words in ("not-a-list", [123], [None]):
        try:
            build_public_transcript(doc, bad_words, [], False)
            raise AssertionError(f"expected ValueError for {bad_words!r}")
        except ValueError:
            pass
    for bad_speakers in ("甲", [123]):
        try:
            build_public_transcript(doc, [], bad_speakers, False)
            raise AssertionError(f"expected ValueError for {bad_speakers!r}")
        except ValueError:
            pass
    # empty inputs are allowed: the transcript passes through untouched
    res = build_public_transcript(DOC, [], [], False)
    assert res["summary"]["hitCount"] == 0
    assert _by_id(res)["s1"]["text"] == "后来 2003 年"
    # blank / duplicate word rows are ignored
    res2 = build_public_transcript(DOC, ["  ", "改制", "改 制"], [], False)
    assert res2["sensitiveWords"] == ["改制"]


def test_api():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "公开版访谈"}).json()
        pid = p["id"]
        c.put(f"/api/projects/{pid}",
              json={"name": "公开版访谈", "document": DOC,
                    "documentVersion": p["documentVersion"]})

        body = {"sensitiveWords": ["后来改制", "年 改制"],
                "anonymizeSpeakers": ["甲", "乙"]}
        r = c.post(f"/api/projects/{pid}/public-transcript/preview", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert data["projectName"] == "公开版访谈"
        # whitespace inside a word is ignored: "年 改制" == "年改制"
        assert data["sensitiveWords"] == ["后来改制", "年改制"]
        segs = {s["id"]: s for s in data["segments"]}
        assert segs["s1"]["text"] == "后来 2003 ■"
        assert segs["s2"]["text"] == "■■ 方案"
        assert segs["s3"]["text"] == "■■■■"
        assert segs["s5"]["text"] == "■■■■"
        assert segs["s1"]["speaker"] == "说话人 1"
        assert segs["s5"]["speaker"] == "说话人 2"
        assert segs["s7"]["speaker"] == "丙"
        mapping = {sp["original"]: sp["alias"] for sp in data["speakers"]}
        assert mapping == {"甲": "说话人 1", "乙": "说话人 2"}
        assert data["summary"]["segmentCount"] == 7
        assert "annotations" not in data

        # download: attachment with the segments-only public document,
        # aliases identical to the preview
        r = c.post(f"/api/projects/{pid}/public-transcript/download", json=body)
        assert r.status_code == 200, r.text
        cd = r.headers["content-disposition"]
        assert "attachment" in cd and "%E5%85%AC%E5%BC%80%E7%89%88" in cd
        pub = r.json()
        assert pub["kind"] == "public-transcript"
        assert set(pub["segments"][0]) == {"id", "start", "end",
                                           "speaker", "text"}
        dl = {s["id"]: s for s in pub["segments"]}
        assert dl["s2"]["text"] == "■■ 方案"
        assert dl["s1"]["speaker"] == "说话人 1"
        assert dl["s5"]["speaker"] == "说话人 2"
        assert "甲" not in r.text and "乙" not in r.text
        assert "后来改制" not in r.text  # masked everywhere it occurred

        # the saved project is unchanged by preview + download
        after = c.get(f"/api/projects/{pid}").json()
        assert after["document"] == DOC

        # empty payload is valid (plain pass-through transcript)
        r = c.post(f"/api/projects/{pid}/public-transcript/preview", json={})
        assert r.status_code == 200
        assert r.json()["summary"]["hitCount"] == 0

        # wrong types -> 422 (pydantic), unknown project -> 404
        assert c.post(f"/api/projects/{pid}/public-transcript/preview",
                      json={"sensitiveWords": "改制"}).status_code == 422
        assert c.post(f"/api/projects/{pid}/public-transcript/preview",
                      json={"anonymizeSpeakers": [123]}).status_code == 422
        assert c.post("/api/projects/nope/public-transcript/preview",
                      json=body).status_code == 404
        assert c.post("/api/projects/nope/public-transcript/download",
                      json=body).status_code == 404

        print("ALL PUBLIC TRANSCRIPT API TESTS PASSED")


def run():
    test_cross_segment_hit_ignores_whitespace()
    test_gap_overlap_and_speaker_switch_break_chains()
    test_overlapping_hits_mask_only_once()
    test_case_sensitivity()
    test_speaker_alias_numbering_is_stable()
    test_segment_identity_and_document_untouched()
    test_public_document_is_segments_only()
    test_invalid_inputs()
    test_api()


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
