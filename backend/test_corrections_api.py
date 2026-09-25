"""End-to-end API tests for the transcript error-word batch correction.

Run with: python3 test_corrections_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

import pytest

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="corrections-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app import corrections  # noqa: E402

DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 5, "speaker": "访谈者",
         "text": "请谈谈你的感受"},
        {"id": "s2", "start": 5, "end": 9, "speaker": "受访者",
         "text": "等的候，他讲的很好"},
        {"id": "s3", "start": 9, "end": 12, "speaker": "受访者",
         "text": "记录完整无误"},
        {"id": "s4", "start": 12, "end": 14, "speaker": "",
         "text": "的士司机"},
    ],
    "annotations": [
        {"id": "a1", "start": 0, "end": 9, "speaker": "受访者",
         "topic": "工厂经历", "note": "重点",
         "source": "rule", "ruleId": "rule-x", "ruleSlot": 0},
    ],
}

PAIRS = [{"from": "的候", "to": "时候"}, {"from": "的", "to": "得"}]


# ---------- engine ----------

def test_normalize_pairs_validation():
    assert corrections.normalize_pairs([]) == []
    assert corrections.normalize_pairs(
        [{"from": " 的候 ", "to": " 时候 "}]) == [{"from": "的候", "to": "时候"}]
    # an empty right word is allowed: it deletes the wrong word
    assert corrections.normalize_pairs(
        [{"from": "嗯", "to": ""}]) == [{"from": "嗯", "to": ""}]

    for bad, needle in (
        ([{"from": "", "to": "时候"}], "未填写错词"),
        ([{"from": "  ", "to": "时候"}], "未填写错词"),
        ([{"from": "", "to": ""}], "未填写错词"),
        ([{"from": "的", "to": "得"}, {"from": "的", "to": "地"}], "多组校正"),
        ([{"from": "的", "to": "得"}, {"from": " 的 ", "to": "地"}], "多组校正"),
    ):
        with pytest.raises(ValueError) as exc:
            corrections.normalize_pairs(bad)
        assert needle in str(exc.value)


def test_correct_text_longest_match_and_single_pass():
    pairs = corrections.normalize_pairs(PAIRS)
    # at the same position the longest wrong word wins: 「的候」->「时候」
    # beats 「的」->「得」; the other 「的」 is still corrected
    corrected, hits = corrections.correct_text("等的候，他讲的很好", pairs)
    assert corrected == "等时候，他讲得很好"
    assert [(h["from"], h["start"], h["end"]) for h in hits] == [
        ("的候", 1, 3), ("的", 6, 7)]

    # replacement output is never rescanned: the 「得的」produced by
    # replacing 「的」must not chain into the longer wrong word
    chain = corrections.normalize_pairs(
        [{"from": "的", "to": "得的"}, {"from": "得的", "to": "X"}])
    corrected, hits = corrections.correct_text("我的", chain)
    assert corrected == "我得的"  # not 「我X」
    assert len(hits) == 1

    # a wrong word introduced by an earlier replacement is not re-matched
    loop = corrections.normalize_pairs(
        [{"from": "甲", "to": "乙"}, {"from": "乙", "to": "丙"}])
    corrected, _ = corrections.correct_text("甲乙", loop)
    assert corrected == "乙丙"  # the 「乙」from 甲->乙 stays; original 乙->丙

    # empty right word deletes the wrong word
    delete = corrections.normalize_pairs([{"from": "嗯", "to": ""}])
    assert corrections.correct_text("嗯，我想想", delete)[0] == "，我想想"

    # no pairs / no text -> no hits
    assert corrections.correct_text("任意", []) == ("任意", [])
    assert corrections.correct_text("", pairs) == ("", [])


def test_transcript_version_changes_on_any_segment_edit():
    base = corrections.transcript_version(DOC["segments"])
    assert corrections.transcript_version(DOC["segments"]) == base

    import copy
    for mutate in (
        lambda segs: segs[0].update(text="改一个字"),
        lambda segs: segs[0].update(speaker="别人"),
        lambda segs: segs[0].update(end=5.5),
        lambda segs: segs.append(
            {"id": "s9", "start": 14, "end": 15, "speaker": "", "text": "新"}),
        lambda segs: segs.pop(0),
    ):
        altered = copy.deepcopy(DOC["segments"])
        mutate(altered)
        assert corrections.transcript_version(altered) != base


def test_preview_corrections():
    preview = corrections.preview_corrections(DOC, PAIRS)
    assert preview["summary"] == {
        "segmentCount": 4, "pairCount": 2, "hitSegmentCount": 3,
        "changedSegmentCount": 3, "hitCount": 4,
    }
    by_id = {s["id"]: s for s in preview["segments"]}
    assert set(by_id) == {"s1", "s2", "s4"}  # s3 has no hit
    assert by_id["s1"]["correctedText"] == "请谈谈你得感受"
    assert by_id["s2"]["correctedText"] == "等时候，他讲得很好"
    assert by_id["s2"]["hitCount"] == 2
    assert by_id["s4"]["correctedText"] == "得士司机"
    assert all(s["changed"] for s in preview["segments"])
    words = {w["from"]: w["count"] for w in preview["words"]}
    assert words == {"的候": 1, "的": 3}
    assert preview["transcriptVersion"] == \
        corrections.transcript_version(DOC["segments"])
    # the source document is untouched
    assert DOC["segments"][1]["text"] == "等的候，他讲的很好"


def test_preview_identity_pair_reports_no_change():
    """A pair whose right word equals the wrong word hits but changes nothing."""
    preview = corrections.preview_corrections(
        DOC, [{"from": "感受", "to": "感受"}])
    assert preview["summary"]["hitSegmentCount"] == 1
    assert preview["summary"]["changedSegmentCount"] == 0
    seg = preview["segments"][0]
    assert seg["id"] == "s1" and seg["changed"] is False
    assert seg["correctedText"] == seg["originalText"]
    # nothing to write for such pairs
    new_texts, replacements = corrections.corrected_texts(
        DOC["segments"], corrections.normalize_pairs([{"from": "感受", "to": "感受"}]))
    assert new_texts == {} and replacements == 0


# ---------- API ----------

def _make_project(client, name="错词校正访谈"):
    project = client.post("/api/projects", json={"name": name}).json()
    client.put(f"/api/projects/{project['id']}",
               json={"name": name, "document": DOC,
                     "documentVersion": project["documentVersion"]})
    return project["id"]


def test_preview_api_validation():
    with TestClient(app) as c:
        pid = _make_project(c)
        # empty wrong word -> 400, duplicate wrong word -> 400
        r = c.post(f"/api/projects/{pid}/corrections/preview",
                   json={"pairs": [{"from": "", "to": "时候"}]})
        assert r.status_code == 400 and "未填写错词" in r.json()["detail"]
        r = c.post(f"/api/projects/{pid}/corrections/preview",
                   json={"pairs": [{"from": "的", "to": "得"},
                                   {"from": "的", "to": "地"}]})
        assert r.status_code == 400 and "多组校正" in r.json()["detail"]
        # same checks on the confirm endpoint
        assert c.post(f"/api/projects/{pid}/corrections/apply",
                      json={"pairs": [{"from": " ", "to": "x"}],
                            "transcriptVersion": "v"}).status_code == 400
        # unknown project -> 404
        assert c.post("/api/projects/nope/corrections/preview",
                      json={"pairs": []}).status_code == 404
        assert c.post("/api/projects/nope/corrections/apply",
                      json={"pairs": [], "transcriptVersion": "v"}
                      ).status_code == 404
        # missing preview version -> 400
        r = c.post(f"/api/projects/{pid}/corrections/apply",
                   json={"pairs": PAIRS})
        assert r.status_code == 400 and "预览" in r.json()["detail"]

        # a valid preview returns hits, corrected text and the version
        r = c.post(f"/api/projects/{pid}/corrections/preview",
                   json={"pairs": PAIRS})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert data["summary"]["changedSegmentCount"] == 3
        assert data["summary"]["hitCount"] == 4
        assert data["transcriptVersion"]
        # preview is read-only: the saved document is unchanged
        saved = c.get(f"/api/projects/{pid}").json()["document"]
        assert saved["segments"][1]["text"] == "等的候，他讲的很好"


def test_apply_then_document_preserves_everything_else():
    with TestClient(app) as c:
        pid = _make_project(c)
        preview = c.post(f"/api/projects/{pid}/corrections/preview",
                         json={"pairs": PAIRS}).json()
        r = c.post(f"/api/projects/{pid}/corrections/apply",
                   json={"pairs": PAIRS,
                         "transcriptVersion": preview["transcriptVersion"]})
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["counts"] == {"segments": 3, "replacements": 4}
        assert sorted(result["changedSegmentIds"]) == ["s1", "s2", "s4"]

        saved = c.get(f"/api/projects/{pid}").json()["document"]
        segs = {s["id"]: s for s in saved["segments"]}
        # texts corrected
        assert segs["s1"]["text"] == "请谈谈你得感受"
        assert segs["s2"]["text"] == "等时候，他讲得很好"
        assert segs["s4"]["text"] == "得士司机"
        assert segs["s3"]["text"] == "记录完整无误"
        # ids, times and speakers preserved
        assert (segs["s2"]["start"], segs["s2"]["end"]) == (5, 9)
        assert segs["s2"]["speaker"] == "受访者"
        assert segs["s4"]["speaker"] == ""
        # annotations untouched, rule provenance included
        assert saved["annotations"] == DOC["annotations"]

        # the version moved on: re-using the old preview version now fails
        r = c.post(f"/api/projects/{pid}/corrections/apply",
                   json={"pairs": PAIRS,
                         "transcriptVersion": preview["transcriptVersion"]})
        assert r.status_code == 409
        assert "重新生成预览" in r.json()["detail"]


def test_apply_rejected_after_transcript_edited_or_reimported():
    with TestClient(app) as c:
        pid = _make_project(c)
        preview = c.post(f"/api/projects/{pid}/corrections/preview",
                         json={"pairs": PAIRS}).json()
        version = preview["transcriptVersion"]

        # 1) the saved transcript is edited (even only a speaker) -> 409
        current = c.get(f"/api/projects/{pid}").json()
        edited = current["document"]
        edited["segments"][0]["speaker"] = "主持人"
        c.put(f"/api/projects/{pid}",
              json={"document": edited,
                    "documentVersion": current["documentVersion"]})
        r = c.post(f"/api/projects/{pid}/corrections/apply",
                   json={"pairs": PAIRS, "transcriptVersion": version})
        assert r.status_code == 409
        assert "重新生成预览" in r.json()["detail"]
        # nothing was written
        assert c.get(f"/api/projects/{pid}").json()[
            "document"]["segments"][1]["text"] == "等的候，他讲的很好"

        # a fresh preview matches the edited transcript and applies
        preview2 = c.post(f"/api/projects/{pid}/corrections/preview",
                          json={"pairs": PAIRS}).json()
        assert preview2["transcriptVersion"] != version
        r = c.post(f"/api/projects/{pid}/corrections/apply",
                   json={"pairs": PAIRS,
                         "transcriptVersion": preview2["transcriptVersion"]})
        assert r.status_code == 200, r.text

        # 2) re-import invalidates the version too
        preview3 = c.post(f"/api/projects/{pid}/corrections/preview",
                          json={"pairs": PAIRS}).json()
        r = c.post(f"/api/projects/{pid}/import",
                   json={"data": [{"id": "n1", "start": 0, "end": 2,
                                   "speaker": "甲", "text": "重新导入的"}]})
        assert r.status_code == 200
        r = c.post(f"/api/projects/{pid}/corrections/apply",
                   json={"pairs": PAIRS,
                         "transcriptVersion": preview3["transcriptVersion"]})
        assert r.status_code == 409


def test_apply_is_atomic_on_failure():
    """A failure while writing leaves the saved document untouched."""
    with TestClient(app) as c:
        pid = _make_project(c)
        preview = c.post(f"/api/projects/{pid}/corrections/preview",
                         json={"pairs": PAIRS}).json()
        pairs = corrections.normalize_pairs(PAIRS)

        # a non-serializable corrected text makes the document UPDATE raise
        # midway; the transaction must roll back with no partial write
        original = corrections.corrected_texts
        corrections.corrected_texts = lambda segs, p: ({1: object()}, 1)
        try:
            with pytest.raises(TypeError):
                db.apply_corrections(
                    pid, pairs, preview["transcriptVersion"])
        finally:
            corrections.corrected_texts = original

        after = c.get(f"/api/projects/{pid}").json()["document"]
        assert after["segments"][1]["text"] == "等的候，他讲的很好"

        # database is still healthy: the same confirm works afterwards
        result = db.apply_corrections(
            pid, pairs, preview["transcriptVersion"])
        assert result["counts"] == {"segments": 3, "replacements": 4}


def run():
    test_normalize_pairs_validation()
    test_correct_text_longest_match_and_single_pass()
    test_transcript_version_changes_on_any_segment_edit()
    test_preview_corrections()
    test_preview_identity_pair_reports_no_change()
    test_preview_api_validation()
    test_apply_then_document_preserves_everything_else()
    test_apply_rejected_after_transcript_edited_or_reimported()
    test_apply_is_atomic_on_failure()
    print("ALL CORRECTION TESTS PASSED")


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
