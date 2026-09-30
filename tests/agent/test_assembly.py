"""assembly 单元测试：skill 装配/preflight、解码参数矩阵、models.json 渲染。"""

import json
from pathlib import Path

import pytest

from agent import assembly
from agent.assembly import PreflightError

VALID_SKILL_YAML = """\
name: fake
description: fake task
required_env: [NEEDED_KEY]
optional_env: [OPT_KEY]
"""


class TestLoadSkill:
    def test_missing_skill_yaml_raises(self, tmp_path: Path):
        _make_task(tmp_path, "nope", None, with_skill_md=False)
        with pytest.raises(PreflightError, match="skill.yaml"):
            _load_skill_from(tmp_path, "nope")

    def test_empty_skill_yaml_raises_mapping_error(self, tmp_path: Path):
        _make_task(tmp_path, "empty", "", with_skill_md=True)
        with pytest.raises(PreflightError):
            _load_skill_from(tmp_path, "empty")

    def test_parses_fields(self, tmp_path: Path):
        _make_task(tmp_path, "fake", VALID_SKILL_YAML, with_skill_md=True)
        spec = _load_skill_from(tmp_path, "fake")
        assert spec.name == "fake"
        assert spec.required_env == ["NEEDED_KEY"]
        assert spec.optional_env == ["OPT_KEY"]

    def test_non_mapping_yaml_raises(self, tmp_path: Path):
        _make_task(tmp_path, "bad", "- just\n- a list\n", with_skill_md=True)
        with pytest.raises(PreflightError, match="mapping"):
            _load_skill_from(tmp_path, "bad")


class TestPreflight:
    def test_passes_with_required_env_present(self, tmp_path: Path):
        _make_task(tmp_path, "fake", VALID_SKILL_YAML, with_skill_md=True)
        spec = _preflight_from(tmp_path, "fake", {"NEEDED_KEY": "1"})
        assert spec.name == "fake"

    def test_missing_required_env_lists_all(self, tmp_path: Path):
        _make_task(tmp_path, "fake", VALID_SKILL_YAML, with_skill_md=True)
        with pytest.raises(PreflightError) as exc:
            _preflight_from(tmp_path, "fake", {})
        assert "NEEDED_KEY" in str(exc.value)

    def test_missing_skill_md_rejected(self, tmp_path: Path):
        _make_task(tmp_path, "fake", VALID_SKILL_YAML, with_skill_md=False)
        with pytest.raises(PreflightError, match="SKILL.md"):
            _preflight_from(tmp_path, "fake", {"NEEDED_KEY": "1"})


class TestDecodeParams:
    def test_defaults_when_unset(self):
        sampling, max_tokens = assembly.decode_params({})
        assert sampling == {"temperature": 0.4, "top_p": 0.95, "frequency_penalty": 0.3}
        assert max_tokens == 16384

    def test_empty_string_falls_back_to_default(self):
        sampling, _ = assembly.decode_params({"DECODE_TEMPERATURE": ""})
        assert sampling["temperature"] == 0.4

    def test_core_override(self):
        sampling, max_tokens = assembly.decode_params(
            {"DECODE_TEMPERATURE": "0.2", "DECODE_MAX_TOKENS": "4096"}
        )
        assert sampling["temperature"] == 0.2
        assert max_tokens == 4096

    def test_optional_absent_when_unset(self):
        sampling, _ = assembly.decode_params({})
        assert "seed" not in sampling and "top_k" not in sampling

    def test_optional_included_when_set(self):
        sampling, _ = assembly.decode_params(
            {
                "DECODE_SEED": "42",
                "DECODE_TOP_K": "50",
                "DECODE_MIN_P": "0.05",
                "DECODE_PRESENCE_PENALTY": "0.1",
                "DECODE_REPETITION_PENALTY": "1.05",
            }
        )
        assert sampling["seed"] == 42
        assert sampling["top_k"] == 50
        assert sampling["min_p"] == 0.05
        assert sampling["presence_penalty"] == 0.1
        assert sampling["repetition_penalty"] == 1.05

    def test_optional_zero_value_is_sent(self):
        # 0 是合法显式值（如 top_p=0），不得当作"未设置"
        sampling, _ = assembly.decode_params({"DECODE_TEMPERATURE": "0"})
        assert sampling["temperature"] == 0.0

    @pytest.mark.parametrize("bad", ["abc", "5", "-0.1"])
    def test_temperature_invalid_rejected(self, bad):
        with pytest.raises(PreflightError):
            assembly.decode_params({"DECODE_TEMPERATURE": bad})

    @pytest.mark.parametrize("bad", ["1.1", "-0.01"])
    def test_top_p_out_of_range_rejected(self, bad):
        with pytest.raises(PreflightError):
            assembly.decode_params({"DECODE_TOP_P": bad})

    @pytest.mark.parametrize("bad", ["x", "-3", "3"])
    def test_frequency_penalty_rejected(self, bad):
        with pytest.raises(PreflightError):
            assembly.decode_params({"DECODE_FREQUENCY_PENALTY": bad})

    @pytest.mark.parametrize("bad", ["0", "63", "200001", "1.5"])
    def test_max_tokens_rejected(self, bad):
        with pytest.raises(PreflightError):
            assembly.decode_params({"DECODE_MAX_TOKENS": bad})

    def test_seed_range(self):
        with pytest.raises(PreflightError):
            assembly.decode_params({"DECODE_SEED": "-1"})
        sampling, _ = assembly.decode_params({"DECODE_SEED": "0"})
        assert sampling["seed"] == 0

    def test_top_k_allows_minus_one(self):
        sampling, _ = assembly.decode_params({"DECODE_TOP_K": "-1"})
        assert sampling["top_k"] == -1

    def test_repetition_penalty_range(self):
        with pytest.raises(PreflightError):
            assembly.decode_params({"DECODE_REPETITION_PENALTY": "0.05"})

    def test_audit_records_null_for_unset_optional(self):
        audit = assembly.decode_audit({})
        assert audit["seed"] is None
        assert audit["temperature"] == 0.4
        assert audit["max_tokens"] == 16384

    def test_audit_records_full_when_set(self):
        audit = assembly.decode_audit({"DECODE_SEED": "7"})
        assert audit["seed"] == 7


class TestRenderModelsJson:
    ENV = {"OPENAI_BASE_URL": "http://ep/v1/", "OPENAI_API_KEY": "k", "AGENT_LLM_MODEL": "m1"}

    def test_renders_provider_with_sampling(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(assembly, "REPO_ROOT", tmp_path)
        path = assembly.render_models_json(self.ENV)
        data = json.loads(path.read_text(encoding="utf-8"))
        provider = data["providers"]["ok-llm"]
        assert provider["baseUrl"] == "http://ep/v1"  # 尾斜杠被去掉
        assert provider["apiKey"] == "$OPENAI_API_KEY"  # 不落明文
        assert provider["models"][0]["id"] == "m1"
        assert provider["models"][0]["samplingParams"]["temperature"] == 0.4
        assert provider["models"][0]["maxTokens"] == 16384

    def test_missing_endpoint_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(assembly, "REPO_ROOT", tmp_path)
        with pytest.raises(PreflightError, match="OPENAI_BASE_URL"):
            assembly.render_models_json({"AGENT_LLM_MODEL": "m"})

    def test_env_decode_overrides_reach_render(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(assembly, "REPO_ROOT", tmp_path)
        env = {**self.ENV, "DECODE_SEED": "7"}
        path = assembly.render_models_json(env)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["providers"]["ok-llm"]["models"][0]["samplingParams"]["seed"] == 7


class TestMisc:
    def test_render_guard_copies_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(assembly, "REPO_ROOT", tmp_path)
        ext = tmp_path / "agent" / "extensions"
        ext.mkdir(parents=True)
        (ext / "bootstrap-guard.ts").write_text("guard-source", encoding="utf-8")
        dst = assembly.render_guard()
        assert dst.read_text(encoding="utf-8") == "guard-source"

    def test_make_run_dir_layout(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(assembly, "REPO_ROOT", tmp_path)
        run_dir = assembly.make_run_dir("ingest", "slug-x", "20260101-000000")
        assert run_dir == tmp_path / "runs" / "20260101-000000" / "ingest" / "slug-x"
        assert run_dir.is_dir()

    def test_make_run_dir_rejects_duplicate(self, tmp_path: Path, monkeypatch):
        # 已存在的 run 目录是审计记录，不允许静默复用/覆盖
        monkeypatch.setattr(assembly, "REPO_ROOT", tmp_path)
        assembly.make_run_dir("ingest", "slug-x", "20260101-000000")
        with pytest.raises(PreflightError):
            assembly.make_run_dir("ingest", "slug-x", "20260101-000000")

    def test_make_run_dir_rejects_bad_chars(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(assembly, "REPO_ROOT", tmp_path)
        with pytest.raises(PreflightError, match="invalid slug"):
            assembly.make_run_dir("ingest", "../escape", "20260101-000000")
        with pytest.raises(PreflightError, match="invalid task"):
            assembly.make_run_dir("../escape", "s", "20260101-000000")


# ---- 辅助：把 tmp_path 注入以隔离 REPO_ROOT（不依赖源码内部实现） ----


def _make_task(root: Path, name: str, yaml_text: str | None, *, with_skill_md: bool):
    d = root / "task" / name
    d.mkdir(parents=True)
    if yaml_text is not None:
        (d / "skill.yaml").write_text(yaml_text, encoding="utf-8")
    if with_skill_md:
        (d / "SKILL.md").write_text("skill body", encoding="utf-8")


def _load_skill_from(root: Path, task: str):
    import unittest.mock as mock

    with mock.patch.object(assembly, "REPO_ROOT", root):
        return assembly.load_skill(task)


def _preflight_from(root: Path, task: str, env: dict):
    import unittest.mock as mock

    with mock.patch.object(assembly, "REPO_ROOT", root):
        return assembly.preflight(task, env)
