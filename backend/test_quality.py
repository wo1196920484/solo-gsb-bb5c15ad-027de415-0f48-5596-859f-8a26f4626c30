"""Unit tests for the annotation quality-check engine (app.quality)."""
from __future__ import annotations

import os

os.environ.setdefault("STATIC_DIR", "/nonexistent")

from app.quality import (  # noqa: E402
    TYPE_OUT_OF_BOUNDS,
    TYPE_OVERLAP,
    TYPE_SPEAKER_MISMATCH,
    TYPE_SPEAKER_MISSING,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    check_document,
)

SEGMENTS = [
    {"id": "s1", "start": 0.0, "end": 5.0, "speaker": "访谈者", "text": "a"},
    {"id": "s2", "start": 5.0, "end": 10.0, "speaker": "受访者", "text": "b"},
    {"id": "s3", "start": 15.0, "end": 20.0, "speaker": "受访者", "text": "c"},
]
# transcript coverage: 0-10 and 15-20 (gap 10-15)


def by_type(result):
    return {q["type"]: q for q in result["issues"]}


def test_clean_document_has_no_issues():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-1", "start": 0.0, "end": 5.0,
             "speaker": "访谈者", "topic": "T"},
            {"id": "ann-2", "start": 15.0, "end": 20.0,
             "speaker": "受访者", "topic": "T2"},
        ],
    }
    result = check_document(doc)
    assert result["issues"] == []
    assert result["summary"] == {
        "total": 0, "error": 0, "warning": 0, "info": 0,
        "outOfBounds": 0, "overlap": 0, "speakerMismatch": 0,
        "speakerMissing": 0, "annotations": 2, "segments": 3,
        "scanLines": result["summary"]["scanLines"], "fixable": 0,
    }
    assert result["window"] == {"start": 0.0, "end": 20.0}
    assert result["summary"]["scanLines"] > 0


def test_out_of_bounds_past_end_is_error_and_fixable():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-x", "start": 18.0, "end": 30.0,
             "speaker": "受访者", "topic": "越界"},
        ],
    }
    q = by_type(check_document(doc))[TYPE_OUT_OF_BOUNDS]
    assert q["severity"] == SEVERITY_ERROR
    assert q["issueId"] == "qc:out_of_bounds:ann-x"
    assert q["fixable"] is True
    assert q["fix"] == {"start": 18.0, "end": 20.0}
    assert q["evidence"]["uncoveredRanges"] == [{"start": 20.0, "end": 30.0}]
    assert q["evidence"]["transcriptCoverage"] == {"start": 0.0, "end": 20.0}
    assert q["annotationIds"] == ["ann-x"]


def test_out_of_bounds_before_start_is_clipped_at_coverage_head():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-y", "start": -2.0, "end": 6.0,
             "speaker": "访谈者", "topic": "前越界"},
        ],
    }
    q = by_type(check_document(doc))[TYPE_OUT_OF_BOUNDS]
    assert q["severity"] == SEVERITY_ERROR
    assert q["fix"] == {"start": 0.0, "end": 6.0}
    assert q["evidence"]["uncoveredRanges"] == [{"start": -2.0, "end": 0.0}]


def test_completely_outside_is_error_and_not_fixable():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-z", "start": 40.0, "end": 50.0,
             "speaker": "受访者", "topic": "天外"},
        ],
    }
    q = by_type(check_document(doc))[TYPE_OUT_OF_BOUNDS]
    assert q["severity"] == SEVERITY_ERROR
    assert q["fixable"] is False
    assert q["fix"] is None
    assert q["evidence"]["coveredRanges"] == []


def test_covering_internal_gap_is_info_and_not_fixable():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-gap", "start": 8.0, "end": 17.0,
             "speaker": "受访者", "topic": "跨空隙"},
        ],
    }
    q = by_type(check_document(doc))[TYPE_OUT_OF_BOUNDS]
    assert q["severity"] == SEVERITY_INFO
    assert q["fixable"] is False
    assert q["evidence"]["uncoveredRanges"] == [{"start": 10.0, "end": 15.0}]


def test_endpoint_touch_is_not_out_of_bounds():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-touch", "start": 5.0, "end": 10.0,
             "speaker": "受访者", "topic": "相接"},
        ],
    }
    assert check_document(doc)["issues"] == []


def test_partial_overlap_pair_is_warning():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-a", "start": 0.0, "end": 6.0,
             "speaker": "访谈者", "topic": "A"},
            {"id": "ann-b", "start": 4.0, "end": 8.0,
             "speaker": "受访者", "topic": "B"},
        ],
    }
    q = by_type(check_document(doc))[TYPE_OVERLAP]
    assert q["severity"] == SEVERITY_WARNING
    assert q["issueId"] == "qc:overlap:ann-a|ann-b"
    assert q["start"] == 4.0 and q["end"] == 6.0
    assert q["evidence"]["overlapSeconds"] == 2.0
    assert q["evidence"]["ranges"] == [{"start": 4.0, "end": 6.0}]
    assert q["annotationIds"] == ["ann-a", "ann-b"]
    assert q["fixable"] is False


def test_containment_overlap_is_error():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-big", "start": 0.0, "end": 5.0,
             "speaker": "访谈者", "topic": "big"},
            {"id": "ann-small", "start": 1.0, "end": 3.0,
             "speaker": "访谈者", "topic": "small"},
        ],
    }
    assert by_type(check_document(doc))[TYPE_OVERLAP]["severity"] == \
        SEVERITY_ERROR


def test_mostly_overlapping_is_error():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-a", "start": 0.0, "end": 10.0,
             "speaker": "访谈者", "topic": "a"},
            {"id": "ann-b", "start": 4.0, "end": 10.0,
             "speaker": "受访者", "topic": "b"},
        ],
    }
    assert by_type(check_document(doc))[TYPE_OVERLAP]["severity"] == \
        SEVERITY_ERROR


def test_speaker_mismatch_all_disagree_is_error():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-sp", "start": 0.0, "end": 5.0,
             "speaker": "受访者", "topic": "错"},
        ],
    }
    q = by_type(check_document(doc))[TYPE_SPEAKER_MISMATCH]
    assert q["severity"] == SEVERITY_ERROR
    assert q["issueId"] == "qc:speaker_mismatch:ann-sp"
    assert q["evidence"]["annotationSpeaker"] == "受访者"
    assert q["evidence"]["segments"][0]["id"] == "s1"
    assert q["evidence"]["segments"][0]["coveredSeconds"] == 5.0
    assert q["evidence"]["segmentSpeakers"] == ["访谈者"]


def test_speaker_mismatch_mixed_speakers_is_warning():
    # covers s1 (访谈者) + s2 (受访者); annotation claims 受访者 -> one
    # disagreeing segment but its own speaker is also present
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-mix", "start": 0.0, "end": 10.0,
             "speaker": "受访者", "topic": "混"},
        ],
    }
    q = by_type(check_document(doc))[TYPE_SPEAKER_MISMATCH]
    assert q["severity"] == SEVERITY_WARNING
    seg_ids = {s["id"] for s in q["evidence"]["segments"]}
    assert seg_ids == {"s1"}


def test_missing_annotation_speaker_is_info():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-nosp", "start": 0.0, "end": 5.0,
             "speaker": "", "topic": "无主"},
        ],
    }
    issues = check_document(doc)["issues"]
    assert len(issues) == 1
    q = issues[0]
    assert q["type"] == TYPE_SPEAKER_MISSING
    assert q["severity"] == SEVERITY_INFO
    assert q["evidence"]["segmentSpeakers"] == ["访谈者"]


def test_missing_speaker_where_segments_also_have_none_is_not_flagged():
    doc = {
        "segments": [{"id": "s0", "start": 0, "end": 3,
                      "speaker": "", "text": "x"}],
        "annotations": [{"id": "a0", "start": 0, "end": 3,
                         "speaker": "", "topic": "t"}],
    }
    assert check_document(doc)["issues"] == []


def test_window_filters_to_range_and_keeps_stable_issue_id():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-x", "start": 18.0, "end": 30.0,
             "speaker": "受访者", "topic": "越界"},
            {"id": "ann-a", "start": 0.0, "end": 6.0,
             "speaker": "访谈者", "topic": "A"},
            {"id": "ann-b", "start": 4.0, "end": 8.0,
             "speaker": "受访者", "topic": "B"},
        ],
    }
    full = check_document(doc)
    windowed = check_document(doc, 12.0, 25.0)
    assert windowed["window"] == {"start": 12.0, "end": 25.0}
    ids_full = {q["issueId"] for q in full["issues"]}
    ids_win = {q["issueId"] for q in windowed["issues"]}
    # overlap (0-8s) is outside the window; the out-of-bounds one is in it
    assert ids_win == {"qc:out_of_bounds:ann-x"}
    assert ids_win <= ids_full  # ids are stable across windows


def test_issues_sorted_by_severity_then_time():
    doc = {
        "segments": SEGMENTS,
        "annotations": [
            {"id": "ann-gap", "start": 8.0, "end": 17.0,
             "speaker": "受访者", "topic": "gap"},          # info
            {"id": "ann-sp", "start": 0.0, "end": 5.0,
             "speaker": "受访者", "topic": "wrong"},        # error
            {"id": "ann-a", "start": 0.0, "end": 6.0,
             "speaker": "访谈者", "topic": "A"},            # warning
            {"id": "ann-b", "start": 4.0, "end": 8.0,
             "speaker": "受访者", "topic": "B"},
        ],
    }
    sev = [q["severity"] for q in check_document(doc)["issues"]]
    # also speaker_missing / mismatch entries may appear; ensure nondecreasing
    rank = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    assert sev == sorted(sev, key=lambda s: rank[s])


def test_oob_only_belongs_to_windows_that_include_uncovered_time():
    doc = {
        "segments": [{"id": "s1", "start": 0.0, "end": 20.0,
                      "speaker": "A", "text": "x"}],
        "annotations": [{"id": "a", "start": 18.0, "end": 30.0,
                         "speaker": "A", "topic": "t"}],
    }
    ids = lambda r: {q["issueId"] for q in r["issues"]}
    expected = {"qc:out_of_bounds:a"}
    assert ids(check_document(doc)) == expected
    assert ids(check_document(doc, 20.5, 25.0)) == expected  # only tail
    assert ids(check_document(doc, 19.0, 22.0)) == expected  # crossing edge
    assert ids(check_document(doc, 18.0, 19.9)) == set()    # covered only


def test_invalid_items_are_ignored():
    doc = {
        "segments": [
            {"id": "bad", "start": 5, "end": 2, "speaker": "x", "text": ""},
            {"id": "ok", "start": 0, "end": 4, "speaker": "x", "text": ""},
        ],
        "annotations": [
            {"id": "nan", "start": "x", "end": 3, "topic": "t"},
            {"id": "fine", "start": 0, "end": 4, "speaker": "x", "topic": "t"},
        ],
    }
    result = check_document(doc)
    assert result["summary"]["segments"] == 1
    assert result["summary"]["annotations"] == 1
    assert result["issues"] == []


def run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"ALL {len(fns)} QUALITY UNIT TESTS PASSED")


if __name__ == "__main__":
    run()
