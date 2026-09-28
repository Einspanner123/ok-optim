"""github_search --probe 官方性判定矩阵（检索接口抽象「官方性判定规则」校准）。

判定规则:
    CodeAvailability ∧ README互认 → official
    README互认 ∧ 作者匹配       → official
    仅作者匹配                   → author_maintained
    单项强证据（README互认 或 CodeAvailability）→ likely（needs_human）
    其余                         → unverified
全部离线：gh_api / load_candidate_raw 打桩。
fixtures 设计注意：中性 owner（sctools）不得与作者姓（doe）撞车。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "task/ingest/scripts"))

import github_search as gs  # noqa: E402

CAND = {
    "paper_title": "Universal Cell Embeddings foundation model",
    "arxiv_id": "2305.16175",
    "authors": ["Jane Doe"],
    "code_availability": False,
}


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


def _gh(meta, readme_text=None, profile=None):
    login = meta.get("owner", {}).get("login", "")

    def fake_gh_api(path, params=None, headers=None):
        if path.startswith("/repos/") and path.endswith("/readme"):
            return FakeResp(200 if readme_text is not None else 404,
                            text=readme_text or "")
        if path.startswith("/repos/"):
            return FakeResp(200, meta)
        if path.startswith("/users/"):
            return FakeResp(200, profile or {})
        raise AssertionError(f"unexpected gh path: {path}")

    return fake_gh_api


def _meta(login="sctools", fork=False, stars=12, description="single-cell tools"):
    return {"owner": {"login": login}, "fork": fork,
            "stargazers_count": stars, "description": description,
            "html_url": f"https://github.com/{login}/repo"}


README_WITH_PHRASE = "we release universal cell embeddings at github.com/x\n"
README_WITH_ARXIV = "preprint arxiv 2305.16175\n"
README_NEUTRAL = "a toolkit for scRNA-seq analysis\n"


@pytest.fixture()
def cand(monkeypatch):
    monkeypatch.setattr(gs, "load_candidate_raw", lambda slug: dict(CAND))


def _probe(monkeypatch, meta, readme_text, cand_over=None, profile=None):
    context = dict(CAND)
    context.update(cand_over or {})
    monkeypatch.setattr(gs, "load_candidate_raw", lambda slug: context)
    monkeypatch.setattr(gs, "gh_api", _gh(meta, readme_text, profile))
    return gs.probe("sctools/repo", "slug")


class TestVerdictMatrix:
    def test_code_availability_and_readme_recognition_official(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), README_WITH_PHRASE,
                   cand_over={"code_availability": True})
        assert r["verdict"] == "official"

    def test_readme_and_author_match_official(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), README_WITH_PHRASE,
                   profile={"name": "Jane Doe"})
        assert r["verdict"] == "official"

    def test_readme_recognition_alone_is_likely(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), README_WITH_PHRASE)
        assert r["verdict"] == "likely"

    def test_code_availability_alone_is_likely(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), None,
                   cand_over={"code_availability": True})
        assert r["verdict"] == "likely"

    def test_author_match_alone_is_author_maintained(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), None, profile={"name": "Jane Doe"})
        assert r["verdict"] == "author_maintained"

    def test_no_evidence_unverified(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), README_NEUTRAL)
        assert r["verdict"] == "unverified"

    def test_readme_via_arxiv_id(self, monkeypatch):
        # 标题短语不在 README，但 arXiv id 出现 → 互认成立
        r = _probe(monkeypatch, _meta(), README_WITH_ARXIV)
        assert r["evidence"]["readme_recognition"] is True
        assert r["verdict"] == "likely"

    def test_unrelated_title_no_recognition(self, monkeypatch):
        # README 只有别的论文短名 → 不构成互认
        r = _probe(monkeypatch, _meta(), "about another model entirely\n")
        assert r["evidence"]["readme_recognition"] is False


class TestEvidenceDetails:
    def test_author_match_uses_family_name(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), None, profile={"name": "Jane Doe"})
        assert "doe" in r["evidence"]["author_match"]

    def test_family_name_too_short_ignored(self, monkeypatch):
        # 作者姓 <3 字符不参与匹配（防误报）
        r = _probe(monkeypatch, _meta(), None,
                   cand_over={"authors": ["Al Wu"]}, profile={"name": "Wu X"})
        assert r["evidence"]["author_match"] is False

    def test_not_fork_evidence(self, monkeypatch):
        r = _probe(monkeypatch, _meta(fork=True), None)
        assert r["evidence"]["not_fork"] is False

    def test_stars_and_description_collected(self, monkeypatch):
        r = _probe(monkeypatch, _meta(stars=99, description="scRNA tools"), None)
        assert r["evidence"]["stars"] == 99
        assert "scRNA" in r["evidence"]["description"]

    def test_repo_404_unverified_with_error(self, monkeypatch):
        context = dict(CAND)
        monkeypatch.setattr(gs, "load_candidate_raw", lambda slug: context)

        def fake_gh(path, params=None, headers=None):
            return FakeResp(404)

        monkeypatch.setattr(gs, "gh_api", fake_gh)
        r = gs.probe("sctools/ghost", "slug")
        assert r["verdict"] == "unverified"
        assert r["evidence"] == {"error": "repo 404"}


class TestParseRepo:
    def test_plain_url(self):
        assert gs.parse_repo("https://github.com/sctools/repo") == "sctools/repo"

    def test_trailing_slash(self):
        assert gs.parse_repo("https://github.com/sctools/repo/") == "sctools/repo"

    def test_non_github_rejected(self):
        with pytest.raises(SystemExit, match="GitHub"):
            gs.parse_repo("https://huggingface.co/x/y")

    def test_dotdot_injection_rejected(self):
        with pytest.raises(SystemExit, match="非法仓库路径"):
            gs.parse_repo("https://github.com/../../etc")


class TestMain:
    def test_likely_prints_needs_human_to_stderr(self, monkeypatch, capsys):
        context = dict(CAND)
        monkeypatch.setattr(gs, "load_candidate_raw", lambda slug: context)
        monkeypatch.setattr(gs, "gh_api", _gh(_meta(), README_WITH_PHRASE))
        monkeypatch.setattr(sys, "argv",
                            ["github_search.py", "https://github.com/sctools/repo", "--probe",
                             "--slug", "slug", "--json"])
        assert gs.main() == 0
        assert "needs_human" in capsys.readouterr().err

    def test_official_exits_clean(self, monkeypatch, capsys):
        context = dict(CAND, code_availability=True)
        monkeypatch.setattr(gs, "load_candidate_raw", lambda slug: context)
        monkeypatch.setattr(gs, "gh_api", _gh(_meta(), README_WITH_PHRASE))
        monkeypatch.setattr(sys, "argv",
                            ["github_search.py", "https://github.com/sctools/repo", "--probe",
                             "--slug", "slug", "--json"])
        assert gs.main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["verdict"] == "official"

