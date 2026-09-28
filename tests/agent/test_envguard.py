"""envguard 单元测试：load_env_file 解析与 build_snapshot 优先级/白名单。"""

import os
from pathlib import Path

from agent import envguard


class TestLoadEnvFile:
    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert envguard.load_env_file(tmp_path / "nope.env") == {}

    def test_basic_key_value(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("FOO=bar\n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"FOO": "bar"}

    def test_comments_and_blank_lines_ignored(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("# comment\n\n  # indented comment\nFOO=bar\n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"FOO": "bar"}

    def test_paired_quotes_stripped(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text('A="x y"\nB=\'z\'\nC=plain\n', encoding="utf-8")
        assert envguard.load_env_file(f) == {"A": "x y", "B": "z", "C": "plain"}

    def test_unpaired_quotes_kept_literal(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text('A="x\n', encoding="utf-8")
        assert envguard.load_env_file(f) == {"A": '"x'}

    def test_line_without_equals_ignored(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("NOT_A_PAIR\nFOO=bar\n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"FOO": "bar"}

    def test_value_may_contain_equals(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("URL=http://x/?a=1&b=2\n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"URL": "http://x/?a=1&b=2"}

    def test_empty_key_ignored(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("=value\nFOO=bar\n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"FOO": "bar"}

    def test_duplicate_key_last_wins(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("FOO=1\nFOO=2\n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"FOO": "2"}

    def test_whitespace_around_key_and_value(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("  FOO =  bar  \n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"FOO": "bar"}

    def test_inline_comment_stripped_after_whitespace(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("DECODE_TEMPERATURE=0.4   # 0.0-2.0; note\n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"DECODE_TEMPERATURE": "0.4"}

    def test_hash_glued_to_value_is_kept(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text("COLOR=#fff\nURL=http://x/#frag\n", encoding="utf-8")
        assert envguard.load_env_file(f) == {"COLOR": "#fff",
                                             "URL": "http://x/#frag"}

    def test_quoted_value_keeps_hash(self, tmp_path: Path):
        f = tmp_path / "a.env"
        f.write_text('SECRET="a#b" # trailing note\n', encoding="utf-8")
        assert envguard.load_env_file(f) == {"SECRET": "a#b"}


class TestBuildSnapshot:
    def test_whitelist_only_passthrough(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("SECRET_SHOULD_NOT_PASS", "nope")
        env = envguard.build_snapshot(dotenv={}, overrides={}, agent_vars={})
        assert env["OPENAI_API_KEY"] == "sk-x"
        assert "SECRET_SHOULD_NOT_PASS" not in env

    def test_dotenv_wins_over_parent_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "from-parent")
        env = envguard.build_snapshot(
            dotenv={"OPENAI_API_KEY": "from-dotenv"}, overrides={}, agent_vars={})
        assert env["OPENAI_API_KEY"] == "from-dotenv"

    def test_overrides_beat_everything(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "from-parent")
        env = envguard.build_snapshot(
            dotenv={"OPENAI_API_KEY": "from-dotenv"},
            overrides={"OPENAI_API_KEY": "from-override"}, agent_vars={})
        assert env["OPENAI_API_KEY"] == "from-override"

    def test_arbitrary_override_keys_allowed(self):
        env = envguard.build_snapshot(
            dotenv={}, overrides={"INGEST_SEED_URL": "arxiv:1"}, agent_vars={})
        assert env["INGEST_SEED_URL"] == "arxiv:1"

    def test_agent_vars_injected(self):
        env = envguard.build_snapshot(
            dotenv={}, overrides={}, agent_vars={"AGENT_TASK": "hello"})
        assert env["AGENT_TASK"] == "hello"

    def test_base_vars_from_parent(self, monkeypatch):
        monkeypatch.setenv("LANG", "C.UTF-8")
        env = envguard.build_snapshot(dotenv={}, overrides={}, agent_vars={})
        assert env["LANG"] == "C.UTF-8"

    def test_venv_injection_prepends_path_and_sets_virtual_env(self, tmp_path: Path):
        env = envguard.build_snapshot(
            dotenv={}, overrides={}, agent_vars={}, venv=tmp_path)
        assert env["VIRTUAL_ENV"] == str(tmp_path)
        assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "bin")

    def test_decode_keys_in_whitelist(self):
        for key in ("DECODE_TEMPERATURE", "DECODE_SEED", "DECODE_MAX_TOKENS"):
            assert key in envguard.CONFIG_KEYS
