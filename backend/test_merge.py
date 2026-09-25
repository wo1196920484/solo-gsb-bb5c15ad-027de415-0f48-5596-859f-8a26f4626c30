"""Tests for the three-way merge logic. Run: python -m pytest backend/"""
import copy

from app.merge import analyze, build_merged, merge_documents

SEG = lambda sid, s, e, sp="", t="x": {
    "id": sid, "start": s, "end": e, "speaker": sp, "text": t}
ANN = lambda aid, s, e, sp="", topic="", note="": {
    "id": aid, "start": s, "end": e, "speaker": sp, "topic": topic, "note": note}


def doc(segs=(), anns=()):
    return {"segments": list(segs), "annotations": list(anns)}


def test_unrelated_edits_merge_automatically():
    base = doc([SEG("s1", 0, 1, "A", "hello"), SEG("s2", 1, 2, "A", "world")])
    a = doc([SEG("s1", 0, 1, "Alice", "hello"), SEG("s2", 1, 2, "A", "world")])
    b = doc([SEG("s1", 0, 1, "A", "hello"), SEG("s2", 1, 2, "Bob", "world!!")])
    r = merge_documents(base, a, b)
    assert r["conflicts"] == []
    merged = {s["id"]: s for s in r["document"]["segments"]}
    assert merged["s1"]["speaker"] == "Alice"
    assert merged["s2"]["speaker"] == "Bob" and merged["s2"]["text"] == "world!!"
    assert r["stats"]["segments"]["modified"] == 2


def test_same_edit_is_not_a_conflict():
    base = doc([SEG("s1", 0, 1, "A", "x")])
    a = doc([SEG("s1", 0, 1, "Alice", "x")])
    r = merge_documents(base, a, copy.deepcopy(a))
    assert r["conflicts"] == []
    assert r["document"]["segments"][0]["speaker"] == "Alice"


def test_overlapping_field_edits_conflict_and_resolves_to_b():
    base = doc([SEG("s1", 0, 1, "A", "hello")])
    a = doc([SEG("s1", 0, 1, "Alice", "hello")])
    b = doc([SEG("s1", 0, 1, "Bob", "hello")])
    changes, conflicts, stats = analyze(base, a, b)
    assert len(conflicts) == 1
    assert conflicts[0]["changedFields"] == ["speaker"]
    cid = conflicts[0]["id"]

    out, unresolved, errors = build_merged(changes, conflicts, {})
    assert unresolved == [cid]  # unresolved -> item omitted
    assert out["segments"] == []

    out, unresolved, errors = build_merged(
        changes, conflicts, {cid: {"choice": "b"}})
    assert unresolved == [] and errors == []
    assert out["segments"][0]["speaker"] == "Bob"


def test_field_level_non_overlap_within_same_item_merges():
    # A changes speaker, B changes text -> different fields -> auto-merge
    base = doc([SEG("s1", 0, 1, "A", "hello")])
    a = doc([SEG("s1", 0, 1, "Alice", "hello")])
    b = doc([SEG("s1", 0, 1, "A", "你好")])
    r = merge_documents(base, a, b)
    assert r["conflicts"] == []
    s = r["document"]["segments"][0]
    assert s["speaker"] == "Alice" and s["text"] == "你好"


def test_modify_vs_delete_is_a_conflict():
    base = doc([SEG("s1", 0, 1, "A", "x")])
    a = doc([SEG("s1", 0, 2, "A", "x")])
    b = doc([])
    changes, conflicts, stats = analyze(base, a, b)
    assert len(conflicts) == 1
    assert conflicts[0]["modifyDelete"] is True
    cid = conflicts[0]["id"]

    # keep A's modification
    out, unresolved, _ = build_merged(changes, conflicts,
                                      {cid: {"choice": "a"}})
    assert out["segments"][0]["end"] == 2

    # honor B's deletion
    out, _, _ = build_merged(changes, conflicts, {cid: {"choice": "b"}})
    assert out["segments"] == []


def test_both_delete():
    base = doc([SEG("s1", 0, 1)])
    r = merge_documents(base, doc(), doc())
    assert r["document"]["segments"] == []
    assert r["conflicts"] == []
    assert r["stats"]["segments"]["deleted"] == 1


def test_independent_annotation_adds_no_overlap_both_kept():
    base = doc()
    a = doc(anns=[ANN("an1", 0, 1, topic="背景")])
    b = doc(anns=[ANN("an2", 5, 6, topic="结论")])
    r = merge_documents(base, a, b)
    assert r["conflicts"] == []
    ids = {x["id"] for x in r["document"]["annotations"]}
    assert ids == {"an1", "an2"}


def test_overlapping_independent_adds_form_interval_conflict():
    base = doc()
    a = doc(anns=[ANN("an1", 0, 10, topic="A的主题")])
    b = doc(anns=[ANN("an2", 5, 15, topic="B的主题")])
    changes, conflicts, stats = analyze(base, a, b)
    assert len(conflicts) == 1
    assert conflicts[0]["conflictType"] == "interval"
    cid = conflicts[0]["id"]

    # choose side A -> B's item removed
    out, _, _ = build_merged(changes, conflicts, {cid: {"choice": "a"}})
    assert [x["id"] for x in out["annotations"]] == ["an1"]

    # keep both
    out, _, _ = build_merged(changes, conflicts, {cid: {"choice": "keep"}})
    assert {x["id"] for x in out["annotations"]} == {"an1", "an2"}

    # drop both
    out, _, _ = build_merged(changes, conflicts, {cid: {"choice": "drop"}})
    assert out["annotations"] == []

    # custom merged value
    out, _, errors = build_merged(changes, conflicts, {cid: {
        "choice": "custom",
        "value": ANN("an3", 2, 12, topic="合并主题", note="折中")}})
    assert errors == []
    assert out["annotations"][0]["topic"] == "合并主题"


def test_touching_intervals_do_not_conflict():
    base = doc()
    a = doc(anns=[ANN("an1", 0, 10)])
    b = doc(anns=[ANN("an2", 10, 20)])
    r = merge_documents(base, a, b)
    assert r["conflicts"] == []
    assert len(r["document"]["annotations"]) == 2


def test_interval_cluster_chains_all_overlapping_items():
    base = doc()
    # an1 overlaps bn1, bn1 overlaps an2 -> one cluster of 4 items
    a = doc(anns=[ANN("an1", 0, 6), ANN("an2", 8, 12)])
    b = doc(anns=[ANN("bn1", 4, 9), ANN("bn2", 100, 101)])
    _, conflicts, _ = analyze(base, a, b)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert {x["id"] for x in c["aItems"]} == {"an1", "an2"}
    assert {x["id"] for x in c["bItems"]} == {"bn1"}


def test_partially_overlapping_fields_can_be_merged_per_field():
    # A changes speaker+note, B changes speaker+topic: speaker conflicts,
    # note and topic auto-merge; speaker adjudicated per field.
    base = doc(anns=[ANN("n1", 0, 1, "A", topic="t", note="base note")])
    a = doc(anns=[ANN("n1", 0, 1, "Alice", topic="t", note="A note")])
    b = doc(anns=[ANN("n1", 0, 1, "Bob", topic="new topic", note="base note")])
    changes, conflicts, _ = analyze(base, a, b)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["conflictingFields"] == ["speaker"]
    assert c["partialMerge"]["note"] == "A note"
    assert c["partialMerge"]["topic"] == "new topic"
    cid = c["id"]

    out, unresolved, errors = build_merged(changes, conflicts, {cid: {
        "choice": "fields", "fields": {"speaker": "b"}}})
    assert errors == [] and unresolved == []
    n = out["annotations"][0]
    assert n["speaker"] == "Bob"
    assert n["note"] == "A note"        # auto-merged from A
    assert n["topic"] == "new topic"    # auto-merged from B


def test_custom_resolution_validation():
    base = doc([SEG("s1", 0, 1, "A", "x")])
    a = doc([SEG("s1", 0, 1, "Alice", "x")])
    b = doc([SEG("s1", 0, 1, "Bob", "x")])
    changes, conflicts, _ = analyze(base, a, b)
    cid = conflicts[0]["id"]

    _, _, errors = build_merged(changes, conflicts, {cid: {
        "choice": "custom", "value": {"start": 5, "end": 1}}})
    assert errors  # end < start and missing fields
    _, unresolved, errors = build_merged(changes, conflicts, {cid: {
        "choice": "custom",
        "value": {"start": 2, "end": 3, "speaker": "X", "text": "ok"}}})
    assert errors == [] and unresolved == []


def test_deletion_and_addition_of_different_items():
    base = doc([SEG("s1", 0, 1), SEG("s2", 1, 2)])
    a = doc([SEG("s2", 1, 2), SEG("s3", 2, 3, t="new")])
    b = doc([SEG("s1", 0, 1), SEG("s2", 1, 2.5)])
    r = merge_documents(base, a, b)
    assert r["conflicts"] == []
    ids = {x["id"] for x in r["document"]["segments"]}
    assert ids == {"s2", "s3"}
    assert next(x for x in r["document"]["segments"] if x["id"] == "s2")["end"] == 2.5


def test_results_sorted_by_time():
    base = doc()
    a = doc(anns=[ANN("an1", 90, 100)])
    b = doc(anns=[ANN("an2", 0, 5), ANN("an3", 40, 50)])
    r = merge_documents(base, a, b)
    starts = [x["start"] for x in r["document"]["annotations"]]
    assert starts == sorted(starts)
