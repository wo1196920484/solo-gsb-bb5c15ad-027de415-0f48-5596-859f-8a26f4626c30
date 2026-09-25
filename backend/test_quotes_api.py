"""End-to-end API tests for the quote search (引语检索).

Run with: python3 test_quotes_api.py  (or via pytest).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="quote-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.quotes import search_quotes  # noqa: E402

# s1+s2 are touching in time with the same speaker -> one chain;
# a gap separates s3; s4 is separated from s3 by a speaker switch (they
# touch in time, so they prove the speaker boundary rule).
DOC = {
    "segments": [
        {"id": "s1", "start": 0, "end": 5, "speaker": "受访者",
         "text": "后来 2003 年"},
        {"id": "s2", "start": 5, "end": 10, "speaker": "受访者",
         "text": "厂子改制了"},
        {"id": "s3", "start": 12, "end": 15, "speaker": "受访者",
         "text": "最早全靠手摇车床"},
        {"id": "s4", "start": 15, "end": 20, "speaker": "访谈者",
         "text": "厂子改制了"},
        {"id": "s5", "start": 20, "end": 25, "speaker": "",
         "text": "厂子改制了"},
    ],
    "annotations": [],
}


def test_engine():
    # cross-segment hit: phrase starts in s1 and ends in s2
    res = search_quotes(DOC, "2003年厂子改制")
    assert res["count"] == 1, res
    m = res["matches"][0]
    assert m["speaker"] == "受访者"
    assert m["start"] == 0.0 and m["end"] == 10.0
    assert m["matchedText"] == "2003年厂子改制"
    assert [s["id"] for s in m["segments"]] == ["s1", "s2"]
    seg1, seg2 = m["segments"]
    # "2003 年" — whitespace dropped; ranges cover original chars only
    assert seg1["text"][seg1["matchStart"]:seg1["matchEnd"]] == "2003 年"
    assert "".join(seg1["text"][a:b] for a, b in seg1["ranges"]) == "2003年"
    assert seg2["ranges"] == [[0, 4]]
    assert "".join(seg2["text"][a:b] for a, b in seg2["ranges"]) == "厂子改制"

    # whitespace inside the query is ignored too
    assert search_quotes(DOC, " 2003 年 ")["count"] == 1

    # the same words by another speaker must NOT join with s3 across the
    # speaker boundary: searching "车床厂子" yields nothing
    assert search_quotes(DOC, "车床厂子改制")["count"] == 0
    # …and "厂子改制" itself only matches inside single segments (no fusion)
    r = search_quotes(DOC, "厂子改制")
    assert {tuple(s["id"] for s in m["segments"]) for m in r["matches"]} == \
        {("s2",), ("s4",), ("s5",)}

    # gap: no chain across s2 -> s3
    assert search_quotes(DOC, "改制最早")["count"] == 0

    # speaker filter
    r = search_quotes(DOC, "厂子改制", speaker="访谈者")
    assert r["count"] == 1
    assert [s["id"] for s in r["matches"][0]["segments"]] == ["s4"]
    assert search_quotes(DOC, "厂子改制", speaker="不存在的人")["count"] == 0

    # case-insensitive by default, opt-in sensitive
    doc2 = {"segments": [
        {"id": "a", "start": 0, "end": 1, "speaker": "X",
         "text": "Hello World"}], "annotations": []}
    assert search_quotes(doc2, "hello world")["count"] == 1
    assert search_quotes(doc2, "hello world", case_sensitive=True)["count"] == 0
    assert search_quotes(doc2, "HelloWorld", case_sensitive=True)["count"] == 1

    # empty query performs no search
    for bad in ("", "   \t\n"):
        try:
            search_quotes(DOC, bad)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    # multiple non-overlapping occurrences inside one segment
    doc3 = {"segments": [
        {"id": "a", "start": 0, "end": 1, "speaker": "X",
         "text": "ab ab ab"}], "annotations": []}
    assert search_quotes(doc3, "ab")["count"] == 3

    # overlapping touching segments do NOT chain
    doc4 = {"segments": [
        {"id": "a", "start": 0, "end": 2, "speaker": "X", "text": "ab"},
        {"id": "b", "start": 1, "end": 3, "speaker": "X", "text": "cd"}],
        "annotations": []}
    assert search_quotes(doc4, "abcd")["count"] == 0


def test_api():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "引语检索访谈"}).json()
        pid = p["id"]
        c.put(f"/api/projects/{pid}",
              json={"name": "引语检索访谈", "document": DOC,
                    "documentVersion": p["documentVersion"]})

        r = c.post(f"/api/projects/{pid}/quote-search",
                   json={"query": "2003年厂子改制"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert data["projectName"] == "引语检索访谈"
        assert data["count"] == 1
        m = data["matches"][0]
        assert m["start"] == 0.0 and m["end"] == 10.0
        assert [s["id"] for s in m["segments"]] == ["s1", "s2"]

        # speaker filter
        r = c.post(f"/api/projects/{pid}/quote-search",
                   json={"query": "厂子改制", "speaker": "访谈者"})
        assert r.json()["count"] == 1

        # no matches
        r = c.post(f"/api/projects/{pid}/quote-search",
                   json={"query": "完全不存在的一句话"})
        assert r.status_code == 200
        assert r.json()["count"] == 0

        # empty / whitespace query -> 400, no search performed
        assert c.post(f"/api/projects/{pid}/quote-search",
                      json={"query": ""}).status_code == 400
        assert c.post(f"/api/projects/{pid}/quote-search",
                      json={"query": "   \n\t "}).status_code == 400

        # request document overrides the saved one (unsaved editor state)
        dirty = {"segments": [
            {"id": "n1", "start": 0, "end": 2, "speaker": "受访者",
             "text": "刚输入尚未保存"},
            {"id": "n2", "start": 2, "end": 4, "speaker": "受访者",
             "text": "的新片段"}],
            "annotations": []}
        r = c.post(f"/api/projects/{pid}/quote-search",
                   json={"query": "尚未保存的新片段", "document": dirty})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["count"] == 1
        assert [s["id"] for s in data["matches"][0]["segments"]] == \
            ["n1", "n2"]
        assert data["matches"][0]["start"] == 0.0
        assert data["matches"][0]["end"] == 4.0

        # unknown project -> 404
        assert c.post("/api/projects/nope/quote-search",
                      json={"query": "x"}).status_code == 404

        print("ALL QUOTE SEARCH API TESTS PASSED")


def run():
    test_engine()
    test_api()


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
