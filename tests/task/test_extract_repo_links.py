"""extract_repo_links 测试：仓库链接提取与 Code Availability 段落（离线）。

extract() 依赖 pypdf 读 PDF——用 FakeReader 打桩，正文即测试文本。
main() 流程用隔离 candidate 目录（monkeypatch _state 入口）。
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "task/ingest/scripts"))

import extract_repo_links as erl  # noqa: E402

FULLTEXT = """
Universal Cell Embeddings: A Foundation Model
Abstract. We release code at https://github.com/sctools/uce.
Also mirrored on https://www.huggingface.co/sctools/uce.
Data at https://zenodo.org/record/12345.
Cite as doi:10.1038/s41586-025-99999-9 and see https://example.com/landing.

Code availability

Source code is available at https://github.com/sctools/uce,
and weights at https://huggingface.co/sctools/uce/tree/main.
"""


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


def _reader_for(*texts):
    pages = [FakePage(t) for t in texts]
    return lambda p: type("R", (), {"pages": pages})


def _extract(*texts):
    with patch("pypdf.PdfReader", _reader_for(*texts)):
        return erl.extract(Path("unused.pdf"))


class TestExtract:
    def test_links_extracted_and_deduped(self):
        r = _extract(FULLTEXT)
        # 正文 3 个 + availability 段的 tree/main 变体（不同 URL，均合法）
        assert r["links"] == ["https://github.com/sctools/uce",
                              "https://www.huggingface.co/sctools/uce",
                              "https://zenodo.org/record/12345",
                              "https://huggingface.co/sctools/uce/tree/main"]

    def test_trailing_period_stripped(self):
        r = _extract(FULLTEXT)
        assert not any(u.endswith(".") for u in r["links"])

    def test_non_repo_hosts_excluded(self):
        r = _extract(FULLTEXT)
        assert not any("example.com" in u for u in r["links"])
        assert not any("doi.org" in u for u in r["links"])

    def test_availability_paragraph_captured(self):
        r = _extract(FULLTEXT)
        assert "Source code is available" in r["availability"]

    def test_no_links_means_none_evidence_contract(self):
        r = _extract("a paper about biology with no links")
        assert r["links"] == []
        assert r["availability"] == ""
        assert r["pages"] == 1

    def test_multi_page_concatenated(self):
        r = _extract("https://github.com/sctools/a", "")
        assert r["pages"] == 2
        assert r["links"] == ["https://github.com/sctools/a"]


@pytest.fixture()
def cand(tmp_path: Path, monkeypatch):
    cdir = tmp_path / "candidates" / "uce"
    (cdir / "paper").mkdir(parents=True)
    (cdir / "paper" / "uce.pdf").write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(erl, "load_candidate_raw", lambda slug: {"slug": "uce"})
    monkeypatch.setattr(erl, "candidate_dir", lambda slug: cdir)
    return cdir


def _run_main(texts, cand):
    with patch("pypdf.PdfReader", _reader_for(*texts)):
        monkeypatch_argv = patch.object(sys, "argv",
                                        ["extract_repo_links.py", "uce", "--json"])
        with monkeypatch_argv:
            return erl.main()


class TestMain:
    def test_found_payload_and_cache(self, cand, capsys):
        assert _run_main([FULLTEXT], cand) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["found"] is True
        assert payload["links"][0] == "https://github.com/sctools/uce"
        assert (cand / "cache" / "fulltext.txt").is_file()
        assert "none_evidence" not in payload

    def test_no_links_records_none_evidence(self, cand, capsys):
        assert _run_main(["no links here"], cand) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["found"] is False
        assert payload["none_evidence"] == "全文无仓库链接"

    def test_missing_pdf_is_fatal(self, tmp_path: Path, monkeypatch, capsys):
        cdir = tmp_path / "c" / "empty"
        cdir.mkdir(parents=True)
        monkeypatch.setattr(erl, "load_candidate_raw", lambda slug: {})
        monkeypatch.setattr(erl, "candidate_dir", lambda slug: cdir)
        monkeypatch.setattr(sys, "argv", ["extract_repo_links.py", "empty", "--json"])
        assert erl.main() == 3
        assert "无 PDF" in capsys.readouterr().err
