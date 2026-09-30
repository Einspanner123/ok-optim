"""github_search --probe 官方性判定矩阵（检索接口抽象「官方性判定规则」校准）。

判定规则:
    CodeAvailability ∧ README互认 → official
    README互认 ∧ 作者匹配       → official
    仅作者匹配                   → author_maintained
    单项强证据（README互认 或 CodeAvailability）→ likely（needs_human）
    其余                         → unverified
全部离线：gh_api 打桩，证据上下文经 context dict / CLI 参数传入（无状态）。
fixtures 设计注意：中性 owner（sctools）不得与作者姓（doe）撞车。
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "task/ingest/scripts"))

import github_search as gs  # noqa: E402

CONTEXT = {
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


def _meta(login="sctools", fork=False, stars=12, description="single-cell tools",
          full_name=None):
    name = full_name or f"{login}/repo"
    return {"owner": {"login": login}, "fork": fork,
            "stargazers_count": stars, "description": description,
            "full_name": name,
            "html_url": f"https://github.com/{name}"}


README_WITH_PHRASE = "we release universal cell embeddings at github.com/x\n"
README_WITH_ARXIV = "preprint arxiv 2305.16175\n"
README_NEUTRAL = "a toolkit for scRNA-seq analysis\n"


def _probe(monkeypatch, meta, readme_text, context_over=None, profile=None):
    context = {**CONTEXT, **(context_over or {})}
    monkeypatch.setattr(gs, "gh_api", _gh(meta, readme_text, profile))
    return gs.probe("sctools/repo", context)


class TestVerdictMatrix:
    def test_code_availability_and_readme_recognition_official(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), README_WITH_PHRASE,
                   context_over={"code_availability": True})
        assert r["verdict"] == "official"
        assert "renamed_from" not in r

    def test_readme_and_author_match_official(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), README_WITH_PHRASE,
                   profile={"name": "Jane Doe"})
        assert r["verdict"] == "official"

    def test_readme_recognition_alone_is_likely(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), README_WITH_PHRASE)
        assert r["verdict"] == "likely"

    def test_code_availability_alone_is_likely(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), None,
                   context_over={"code_availability": True})
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


class TestCanonicalRename:
    def test_meta_full_name_differs_marks_renamed_from(self, monkeypatch):
        # gh api 返回的 canonical 全名 ≠ 请求名 → renamed_from 标注，
        # 且去重键（repo 字段）取 canonical 新地址
        r = _probe(monkeypatch, _meta(full_name="sctools/repo-moved"),
                   README_WITH_PHRASE, profile={"name": "Jane Doe"})
        assert r["renamed_from"] == "sctools/repo"
        assert r["repo"] == "sctools/repo-moved"
        assert r["repo_url"] == "https://github.com/sctools/repo-moved"

    def test_case_only_difference_is_not_a_rename(self, monkeypatch):
        r = _probe(monkeypatch, _meta(full_name="SCTools/Repo"),
                   README_WITH_PHRASE)
        assert "renamed_from" not in r


class TestEvidenceDetails:
    def test_author_match_uses_family_name(self, monkeypatch):
        r = _probe(monkeypatch, _meta(), None, profile={"name": "Jane Doe"})
        assert "doe" in r["evidence"]["author_match"]

    def test_family_name_too_short_ignored(self, monkeypatch):
        # 作者姓 <3 字符不参与匹配（防误报）
        r = _probe(monkeypatch, _meta(), None,
                   context_over={"authors": ["Al Wu"]}, profile={"name": "Wu X"})
        assert r["evidence"]["author_match"] is False

    def test_not_fork_evidence(self, monkeypatch):
        r = _probe(monkeypatch, _meta(fork=True), None)
        assert r["evidence"]["not_fork"] is False

    def test_stars_and_description_collected(self, monkeypatch):
        r = _probe(monkeypatch, _meta(stars=99, description="scRNA tools"), None)
        assert r["evidence"]["stars"] == 99
        assert "scRNA" in r["evidence"]["description"]

    def test_repo_404_unverified_with_error(self, monkeypatch):
        def fake_gh(path, params=None, headers=None):
            return FakeResp(404)

        monkeypatch.setattr(gs, "gh_api", fake_gh)
        r = gs.probe("sctools/ghost", dict(CONTEXT))
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
    def _argv(self, extra):
        return ["github_search.py", "https://github.com/sctools/repo", "--probe",
                "--title", CONTEXT["paper_title"], "--arxiv-id", CONTEXT["arxiv_id"],
                "--authors", "Jane Doe", *extra, "--json"]

    def test_likely_prints_needs_human_to_stderr(self, monkeypatch, capsys):
        monkeypatch.setattr(gs, "gh_api", _gh(_meta(), README_WITH_PHRASE))
        monkeypatch.setattr(sys, "argv", self._argv([]))
        assert gs.main() == 0
        assert "needs_human" in capsys.readouterr().err

    def test_official_exits_clean(self, monkeypatch, capsys):
        monkeypatch.setattr(gs, "gh_api", _gh(_meta(), README_WITH_PHRASE))
        monkeypatch.setattr(sys, "argv", self._argv(["--code-availability", "yes"]))
        assert gs.main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["verdict"] == "official"


# ---- HTTP 缓存透明性与易变端点豁免 ----

import _net  # noqa: E402


@pytest.fixture()
def cache_root(tmp_path):
    """隔离的缓存根，避免污染 runs/.cache。"""
    with patch.object(_net, "CACHE_ROOT", tmp_path / "cache"):
        yield tmp_path / "cache"


def _fake_response():
    return httpx.Response(200, content=b'{"ok": 1}',
                          request=httpx.Request("GET", "https://x/y"))


@pytest.mark.parametrize("url", [
    "https://api.github.com/repos/a/b",
    "https://raw.githubusercontent.com/a/b/main/README.md",
    "https://huggingface.co/a/b",
])
def test_volatile_hosts_not_cacheable(url):
    assert not _net._cacheable(url)


@pytest.mark.parametrize("url", [
    "https://api.semanticscholar.org/graph/v1/paper/x",
    "https://export.arxiv.org/api/query",
    "https://api.unpaywall.org/v2/10.1/x",
])
def test_stable_hosts_cacheable(url):
    assert _net._cacheable(url)


def test_volatile_endpoint_not_written_to_disk(cache_root):
    with patch.object(_net, "_throttle"), \
            patch.object(_net, "_get", return_value=_fake_response()):
        _net.http_get("https://api.github.com/repos/a/b")
    assert not cache_root.exists()


def test_stable_endpoint_written_atomically(cache_root):
    with patch.object(_net, "_throttle"), \
            patch.object(_net, "_get", return_value=_fake_response()):
        _net.http_get("https://api.semanticscholar.org/graph/v1/paper/x")
    assert len(list(cache_root.glob("*.cache"))) == 1
    assert list(cache_root.glob("*.tmp")) == []


def test_cache_hit_is_indistinguishable(cache_root):
    """缓存透明：第二次零网络、内容一致、无伪造标记头。"""
    with patch.object(_net, "_throttle"), \
            patch.object(_net, "_get", return_value=_fake_response()) as get:
        first = _net.http_get("https://api.semanticscholar.org/graph/v1/paper/x")
        second = _net.http_get("https://api.semanticscholar.org/graph/v1/paper/x")
    assert get.call_count == 1
    assert first.content == second.content
    assert "X-Cache" not in second.headers


def test_cache_false_bypasses_cache(cache_root):
    with patch.object(_net, "_throttle"), \
            patch.object(_net, "_get", return_value=_fake_response()) as get:
        _net.http_get("https://api.semanticscholar.org/graph/v1/paper/x",
                      cache=False)
    assert get.call_count == 1
    assert not cache_root.exists()
