"""P1 检索脚本解析测试：scholar_lookup 种子分流/S2 映射 + search_arxiv Atom 解析
+ download_pdf 多源链（全部离线，http_get/http_get_stream 打桩）。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "task/ingest/scripts"))

import download_pdf as dp
import scholar_lookup as sl
import search_arxiv as sa


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=None)


# ---- scholar_lookup.classify（种子五形态分流） ----


class TestClassify:
    @pytest.mark.parametrize(
        "seed,expected",
        [
            ("doi:10.1038/x", "DOI:10.1038/x"),
            ("arxiv:2305.16175", "ARXIV:2305.16175"),
            ("pmid:123456", "PMID:123456"),
        ],
    )
    def test_prefixed_forms(self, seed, expected):
        assert sl.classify(seed) == ("paper", expected)

    def test_arxiv_abs_url(self):
        assert sl.classify("https://arxiv.org/abs/2305.16175") == ("paper", "ARXIV:2305.16175")

    def test_arxiv_pdf_url(self):
        assert sl.classify("https://arxiv.org/pdf/2305.16175v2") == ("paper", "ARXIV:2305.16175")

    def test_doi_url(self):
        assert sl.classify("https://doi.org/10.1038/s41586-025-9") == (
            "paper",
            "DOI:10.1038/s41586-025-9",
        )

    def test_unresolvable_url_needs_human(self):
        with pytest.raises(SystemExit, match="needs_human"):
            sl.classify("https://example.com/paper")

    def test_title_or_keywords_go_search(self):
        assert sl.classify("single cell foundation model") == (
            "search",
            "single cell foundation model",
        )


# ---- scholar_lookup.compact（S2 → 候选字段映射） ----


class TestCompact:
    S2 = {
        "title": "A model",
        "year": 2025,
        "venue": "Nature",
        "externalIds": {"DOI": "10.1/x", "ArXiv": "2501.1", "PubMed": "77"},
        "openAccessPdf": {"url": "https://oa/pdf"},
        "citationCount": 9,
        "abstract": "abs",
        "authors": [{"name": "A B"}, {"name": "C D"}],
        "url": "https://s2/x",
    }

    def test_field_mapping_and_paper_url_prefers_doi(self):
        c = sl.compact(self.S2)
        assert c["paper_url"] == "https://doi.org/10.1/x"
        assert c["arxiv_id"] == "2501.1"
        assert c["openaccesspdf_url"] == "https://oa/pdf"
        assert c["authors"] == ["A B", "C D"]

    def test_paper_url_falls_back_to_arxiv_then_url(self):
        p = {**self.S2, "externalIds": {"ArXiv": "2501.1"}, "openAccessPdf": None}
        assert sl.compact(p)["paper_url"] == "https://arxiv.org/abs/2501.1"
        p2 = {**self.S2, "externalIds": {}, "openAccessPdf": None}
        assert sl.compact(p2)["paper_url"] == "https://s2/x"

    def test_none_fields_safe(self):
        c = sl.compact({})
        assert c["title"] is None and c["authors"] == []
        assert c["openaccesspdf_url"] is None


# ---- search_arxiv（Atom 解析 + 年份过滤） ----

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/abs/2501.11111v1</id>
    <published>2025-01-10T00:00:00Z</published>
    <title>  Model A  </title>
    <author><name>Ann</name></author><author><name>Bob</name></author>
    <summary>  summary a  </summary></entry>
  <entry><id>http://arxiv.org/abs/2301.22222v2</id>
    <published>2023-06-01T00:00:00Z</published>
    <title>Model B</title>
    <author><name>Cid</name></author>
    <summary>summary b</summary></entry>
</feed>"""


class TestSearchArxiv:
    def _run(self, monkeypatch, y_from=None, y_to=None, limit=10):
        seen = {}

        def fake_get(url, params=None, headers=None):
            seen["params"] = params
            return FakeResp(200, text=ATOM)

        monkeypatch.setattr(sa, "http_get", fake_get)
        monkeypatch.setenv("INGEST_YEAR_FROM", y_from or "")
        monkeypatch.setenv("INGEST_YEAR_TO", y_to or "")
        out = sa.search("query", limit, y_from, y_to)
        return out, seen

    def test_parse_fields_and_id_version_stripped(self, monkeypatch):
        out, _ = self._run(monkeypatch)
        assert out[0]["arxiv_id"] == "2501.11111"
        assert out[0]["title"] == "Model A"
        assert out[0]["authors"] == ["Ann", "Bob"]
        assert out[0]["paper_url"] == "https://arxiv.org/abs/2501.11111"

    def test_year_window_filters(self, monkeypatch):
        out, _ = self._run(monkeypatch, y_from="2024")
        assert [p["arxiv_id"] for p in out] == ["2501.11111"]
        out, _ = self._run(monkeypatch, y_to="2024")
        assert [p["arxiv_id"] for p in out] == ["2301.22222"]

    def test_limit_applies_after_filter(self, monkeypatch):
        out, _ = self._run(monkeypatch, limit=1)
        assert len(out) == 1

    def test_request_carries_sort_and_encoding_headers(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, headers=None):
            captured.update(url=url, params=params, headers=headers)
            return FakeResp(200, text="<feed></feed>")

        monkeypatch.setattr(sa, "http_get", fake_get)
        monkeypatch.setenv("INGEST_YEAR_FROM", "")
        monkeypatch.setenv("INGEST_YEAR_TO", "")
        sa.search("q", 5, None, None)
        assert captured["params"]["sortBy"] == "submittedDate"
        assert captured["headers"]["Accept-Encoding"] == "gzip, deflate"


# ---- download_pdf（多源链顺序、校验、needs_manual） ----

PDF = b"%PDF-1.4 " + b"x" * 60000


class TestChainUrls:
    def test_priority_order(self, monkeypatch):
        monkeypatch.setenv("EMAIL", "a@b.c")
        cand = {
            "openaccesspdf_url": "https://oa/1",
            "arxiv_id": "2501.1",
            "citation_pdf_url": "https://j/pdf",
            "doi": "10.1/x",
        }
        order = [s for s, _ in dp.chain_urls(cand)]
        assert order == ["s2_openaccesspdf", "arxiv_pdf", "citation_pdf_url", "unpaywall"]

    def test_unpaywall_needs_email(self, monkeypatch):
        monkeypatch.delenv("EMAIL", raising=False)
        cand = {"doi": "10.1/x"}
        assert [s for s, _ in dp.chain_urls(cand)] == []


class TestTryChain:
    def _dp_cand(self, cand):
        return cand

    def test_first_source_wins(self, monkeypatch):
        monkeypatch.setattr(dp, "http_get_stream", lambda url, timeout=30: PDF)
        data, source = dp.try_chain({"openaccesspdf_url": "https://oa/1", "arxiv_id": "1"})
        assert source == "s2_openaccesspdf" and data == PDF

    def test_skips_bad_pdf_and_non_200(self, monkeypatch):
        calls = []

        def fake_stream(url, timeout=30):
            calls.append(url)
            if url.startswith("https://oa"):
                return b"not a pdf"
            return PDF

        monkeypatch.setattr(dp, "http_get_stream", fake_stream)
        monkeypatch.setattr(dp, "http_get", lambda url, **k: FakeResp(404))
        _data, source = dp.try_chain({"openaccesspdf_url": "https://oa/1", "arxiv_id": "2501.1"})
        assert source == "arxiv_pdf" and len(calls) == 2

    def test_all_fail_returns_none(self, monkeypatch):
        monkeypatch.setattr(dp, "http_get_stream", lambda url, timeout=30: b"junk")
        monkeypatch.setattr(dp, "http_get", lambda url, **k: FakeResp(404))
        assert (
            dp.try_chain(
                {"openaccesspdf_url": "https://oa/1", "arxiv_id": "1", "paper_url": "https://j/x"}
            )
            is None
        )

    def test_unpaywall_resolves_real_pdf_url(self, monkeypatch):
        monkeypatch.setenv("EMAIL", "a@b.c")

        def fake_get(url, **k):
            return FakeResp(200, payload={"oa_locations": [{"url_for_pdf": "https://real/pdf"}]})

        monkeypatch.setattr(dp, "http_get", fake_get)
        monkeypatch.setattr(dp, "http_get_stream", lambda url, timeout=30: PDF)
        data, source = dp.try_chain({"doi": "10.1/x"})
        assert source.startswith("unpaywall→") and data == PDF

    def test_too_small_pdf_rejected(self, monkeypatch):
        monkeypatch.setattr(dp, "http_get_stream", lambda url, timeout=30: b"%PDF tiny")
        assert dp.try_chain({"arxiv_id": "1"}) is None
