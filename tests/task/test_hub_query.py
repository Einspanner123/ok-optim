"""hub_query / readers 仓库主键去重查询测试（离线）。

去重主键 = 归一化仓库地址 owner/repo（小写）；model_name 占用是身份类人工闸。
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "task/ingest/scripts"))

import hub_query
from _fixtures import row

from hubkit import readers
from hubkit.schema import CSV_COLUMNS, MODELS_CSV


def _hub(tmp_path: Path, *items):
    hub = tmp_path / "single-cell-hub"
    csv_path = hub / MODELS_CSV
    csv_path.parent.mkdir(parents=True)
    import csv

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for item in items:
            writer.writerow(item)
    return hub


class TestRepoKey:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://github.com/Example/Repo", "example/repo"),
            ("http://github.com/example/repo/", "example/repo"),
            ("https://www.github.com/example/repo", "example/repo"),
            ("github.com/example/repo", "example/repo"),
            ("https://github.com/example/repo.git", "example/repo"),
            ("https://github.com/example/repo/tree/main", "example/repo"),
            ("example/repo", "example/repo"),
            ("EXAMPLE/REPO", "example/repo"),
        ],
    )
    def test_github_forms_normalize(self, url, expected):
        assert readers.repo_key(url) == expected

    def test_non_github_keeps_lowercased_url(self):
        assert readers.repo_key("https://HuggingFace.co/x/y") == "https://huggingface.co/x/y"

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            readers.repo_key("  ")

    def test_dotdot_not_treated_as_bare_repo(self):
        # 带路径注入形态不归一为 owner/repo，原样小写返回（不用于匹配）
        assert readers.repo_key("../../etc") == "../../etc"


class TestFinders:
    def test_find_by_repo_matches_normalized(self, tmp_path):
        hub = _hub(tmp_path, row())
        assert readers.find_by_repo(hub, "https://github.com/example/Seed.git") is not None
        assert readers.find_by_repo(hub, "https://github.com/example/Other") is None

    def test_find_by_model_case_insensitive(self, tmp_path):
        hub = _hub(tmp_path, row())
        assert readers.find_by_model(hub, "seed") is not None
        assert readers.find_by_model(hub, "SEED") is not None
        assert readers.find_by_model(hub, "Other") is None

    def test_list_entries_compact_and_ordered(self, tmp_path):
        hub = _hub(tmp_path, row("Seed"), row("Zeta"))
        entries = readers.list_entries(hub)
        assert [e["model_name"] for e in entries] == ["Seed", "Zeta"]
        assert set(entries[0]) == {"model_name", "paper_title", "year", "venue", "repo_url"}

    def test_header_mismatch_raises(self, tmp_path):
        hub = tmp_path / "hub"
        (hub / MODELS_CSV).parent.mkdir(parents=True)
        (hub / MODELS_CSV).write_text("wrong,header\n", encoding="utf-8")
        with pytest.raises(ValueError, match="表头"):
            readers.find_by_repo(hub, "example/repo")


class TestCli:
    def _run(self, argv, hub):
        with patch.object(sys, "argv", ["hub_query.py", "--hub", str(hub), *argv]):
            return hub_query.main()

    def test_repo_found_and_miss(self, tmp_path, capsys):
        hub = _hub(tmp_path, row())
        assert self._run(["--repo", "https://github.com/example/Seed/", "--json"], hub) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "hub": str(hub),
            "query": {"repo": "example/seed"},
            "found": True,
            "match": readers._compact(row()),
        }
        assert self._run(["--repo", "example/ghost", "--json"], hub) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["found"] is False and payload["match"] is None

    def test_model_collision_reported(self, tmp_path, capsys):
        hub = _hub(tmp_path, row())
        assert self._run(["--model", "SEED", "--json"], hub) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["found"] is True
        assert payload["match"]["model_name"] == "Seed"

    def test_list(self, tmp_path, capsys):
        hub = _hub(tmp_path, row())
        assert self._run(["--list", "--json"], hub) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 1 and payload["entries"][0]["model_name"] == "Seed"

    def test_no_mode_is_usage_error(self, tmp_path):
        hub = _hub(tmp_path, row())
        with pytest.raises(SystemExit):
            self._run([], hub)

    def test_missing_hub_is_fatal(self, tmp_path):
        assert self._run(["--list"], tmp_path / "nope") == 3
