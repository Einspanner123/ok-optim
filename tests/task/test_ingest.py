"""无状态 ingest 流程测试：run 目录材料 → stage → apply → run 结果契约。"""

import csv
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "task/ingest/scripts"))
import _entry as entry
import _state as state
import acquire_repo
import audit_scan
from _fixtures import readme, row

from hubkit import readers, render
from hubkit.schema import CSV_COLUMNS, ENTRIES_ROOT, INDEX_FILES, entry_path


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = self.root / "single-cell-hub"
        self.runs = self.root / "runs"
        self.run = self.runs / "t1" / "ingest" / "seed"
        for key, value in {"HUB": self.hub, "RUNS_ROOT": self.runs}.items():
            p = patch.object(state, key, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(
            os.environ,
            {
                "AGENT_RUN_ID": "t1",
                "AGENT_RUN_DIR": str(self.run),
                "AGENT_SLUG": "seed",
                "INGEST_MAX_NEW": "1",
            },
            clear=True,
        )
        p.start()
        self.addCleanup(p.stop)
        self.seed = row()
        self.write_entry(self.seed)
        (self.hub / "README.md").write_text(
            readme(self.seed, "single_cell_models/"), encoding="utf-8"
        )
        (self.hub / ENTRIES_ROOT / "README.md").write_text(readme(self.seed), encoding="utf-8")
        (self.hub / ".gitignore").write_text("", encoding="utf-8")
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(self.seed)
        (self.hub / ENTRIES_ROOT / "models.csv").write_text(buf.getvalue(), encoding="utf-8")
        self.assertTrue(entry.check(self.hub)["ok"], entry.check(self.hub))

    def write_entry(self, item):
        root = self.hub / entry_path(item["model_name"])
        (root / "paper").mkdir(parents=True)
        (root / "repo").mkdir()
        (root / "paper" / f"{item['model_name']}.pdf").write_bytes(b"%PDF" + b"x" * 60000)
        (root / "repo/main.py").write_text("seed = 1\n")
        (root / "README.md").write_text(
            render.render_model_readme(
                item["model_name"],
                item,
                "official",
                "repository cloned at commit " + item["commit_hash"],
            ),
            encoding="utf-8",
        )

    def payload(self, name="New"):
        item = row(name)
        if name != "Seed":
            item["commit_hash"] = "b" * 40
        return {
            **item,
            "verdict": "official",
            "code_availability": "https://github.com/example/" + name,
        }

    def materials(self, pdf_name="paper.pdf"):
        paper = self.run / "paper"
        paper.mkdir(parents=True, exist_ok=True)
        (paper / pdf_name).write_bytes(b"%PDF" + b"y" * 60000)
        repo = self.run / "repo"
        repo.mkdir(exist_ok=True)
        (repo / "main.py").write_text("candidate = 2\n")

    def test_workdir_requires_agent_run_dir(self):
        with patch.dict(os.environ):
            os.environ.pop("AGENT_RUN_DIR", None)
            with self.assertRaisesRegex(state.IngestError, "AGENT_RUN_DIR"):
                state.workdir()

    def test_run_dir_outside_runs_root_rejected(self):
        with patch.dict(os.environ, {"AGENT_RUN_DIR": str(self.root / "elsewhere")}):
            with self.assertRaises(state.IngestError):
                state.workdir()

    def test_stage_requires_entry_fields(self):
        self.materials()
        payload = self.payload()
        payload.pop("license")
        with self.assertRaisesRegex(state.IngestError, "缺少字段"):
            entry.stage(payload)

    def test_apply_without_stage_fails(self):
        self.materials()
        with self.assertRaisesRegex(state.IngestError, "stage_entry"):
            entry.apply()

    def test_stage_does_not_modify_hub_and_apply_uses_exact_files(self):
        self.materials()
        before = entry.digest(self.hub)
        result = entry.stage(self.payload())
        self.assertTrue(result["ok"], result)
        self.assertEqual(entry.digest(self.hub), before)
        staged = self.run / "staged"
        expected = {rel: (staged / rel).read_bytes() for rel in INDEX_FILES}
        with patch.object(
            render, "entry_files", side_effect=AssertionError("apply must not render")
        ):
            self.assertTrue(entry.apply()["applied"])
        for rel, contents in expected.items():
            self.assertEqual((self.hub / rel).read_bytes(), contents)
        self.assertTrue(entry.check(self.hub)["ok"])

    def test_single_pdf_name_need_not_match_model(self):
        self.materials("downloaded.pdf")
        self.assertTrue(entry.stage(self.payload())["ok"])

    def test_multiple_distinct_pdfs_rejected(self):
        self.materials("a.pdf")
        (self.run / "paper" / "b.pdf").write_bytes(b"%PDF" + b"z" * 60000)
        with self.assertRaisesRegex(state.IngestError, "多个不同 PDF"):
            entry.stage(self.payload())

    def test_existing_unrelated_errors_do_not_block_a_valid_new_entry(self):
        (self.hub / entry_path("Seed") / "paper/Seed.pdf").write_bytes(b"bad legacy PDF")
        (self.hub / ENTRIES_ROOT / "Orphan").mkdir()
        self.assertFalse(entry.check(self.hub)["ok"])
        self.materials()
        self.assertTrue(entry.stage(self.payload())["ok"])
        self.assertTrue(entry.apply()["ok"])
        self.assertTrue(entry.check(self.hub, only="New")["ok"])
        self.assertFalse(entry.check(self.hub)["ok"])

    def test_audit_update_replaces_row_and_entry(self):
        self.materials()
        self.assertTrue(entry.stage(self.payload("Seed"))["ok"])
        entry.apply()
        rows, _ = readers.load_models_csv(self.hub)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (self.hub / entry_path("Seed") / "repo/main.py").read_text(), "candidate = 2\n"
        )
        self.assertFalse(audit_scan.scan()["anomalies"])

    def test_hub_change_invalidates_stage(self):
        self.materials()
        entry.stage(self.payload())
        (self.hub / ".gitignore").write_text("# changed\n")
        with self.assertRaisesRegex(state.IngestError, "重新 stage"):
            entry.apply()

    def test_material_change_invalidates_stage(self):
        self.materials()
        entry.stage(self.payload())
        (self.run / "repo/extra.py").write_text("extra = 1\n")
        with self.assertRaisesRegex(state.IngestError, "重新 stage"):
            entry.apply()

    def test_staged_content_change_is_rejected(self):
        self.materials()
        entry.stage(self.payload())
        (self.run / "staged/README.md").write_text("tampered")
        with self.assertRaisesRegex(state.IngestError, "staged 内容已变化"):
            entry.apply()

    def test_post_validation_failure_restores_existing_entry_and_indexes(self):
        self.materials()
        entry.stage(self.payload("Seed"))
        before = entry.digest(self.hub)
        good = {"ok": True, "errors": [], "error_count": 0}
        bad = {"ok": False, "errors": [{"message": "injected failure"}], "error_count": 1}
        with patch.object(entry, "check", side_effect=[good, bad]):
            with self.assertRaises(state.IngestError):
                entry.apply()
        self.assertEqual(entry.digest(self.hub), before)

    def test_github_archive_fallback_uses_commit_and_filters_files(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("repo-abc/main.py", "answer = 42\n")
            archive.writestr("repo-abc/weights/model.bin", "binary")
            archive.writestr("repo-abc/../escape.txt", "bad")
        meta = Mock()
        meta.raise_for_status.return_value = None
        meta.json.return_value = {"default_branch": "main"}
        head = Mock()
        head.raise_for_status.return_value = None
        head.json.return_value = {"sha": "a" * 40}
        target = self.root / "archive"
        target.mkdir()
        with (
            patch.object(acquire_repo, "gh_api", side_effect=[meta, head]),
            patch.object(acquire_repo, "http_get_stream", return_value=blob.getvalue()),
        ):
            result = acquire_repo._github_archive("https://github.com/example/repo", target)
        self.assertEqual(result["commit"], "a" * 40)
        self.assertEqual(result["files"], 1)
        self.assertEqual((target / "main.py").read_text(), "answer = 42\n")
        self.assertFalse((target / "weights/model.bin").exists())
        self.assertFalse((target / "escape.txt").exists())
        with (
            patch.object(
                acquire_repo,
                "_run_git",
                side_effect=__import__("subprocess").CalledProcessError(1, "git"),
            ),
            patch.object(acquire_repo, "_github_archive", return_value=result) as fallback,
        ):
            self.assertEqual(
                acquire_repo._github("https://github.com/example/repo", target), result
            )
            fallback.assert_called_once()

    def test_acquire_repo_reuses_empty_placeholder_only(self):
        repo = self.run / "repo"
        repo.mkdir(parents=True)
        saved = repo / "main.py"
        saved.write_text("old = 1\n")
        self.assertNotEqual(saved.stat().st_size, 0)
        url = "https://github.com/example/New"
        with (
            patch.object(acquire_repo, "load_dotenv"),
            patch.object(sys, "argv", ["acquire_repo.py", url]),
            patch.object(acquire_repo, "_github") as github,
        ):
            self.assertEqual(acquire_repo.main(), 2)
            github.assert_not_called()
            saved.unlink()
            github.side_effect = lambda repo_url, dest: (
                (dest / "main.py").write_text("new = 1\n"),
                {"channel": "github", "commit": "a" * 40, "files": 1},
            )[1]
            self.assertEqual(acquire_repo.main(), 0)
            self.assertEqual((repo / "main.py").read_text(), "new = 1\n")

    def test_three_entries_clear_materials_and_keep_first_evidence(self):
        trace = self.run / "events.jsonl"
        self.run.mkdir(parents=True, exist_ok=True)
        trace.write_text("first entry full trace\n")
        session = self.run / "session.jsonl"
        session.write_text("session evidence\n")
        older = self.runs / "older" / "events.jsonl"
        older.parent.mkdir()
        older.write_text("older evidence\n")
        archive = self.run / "first-applied"
        archive_hash = None
        with patch.dict(os.environ, {"INGEST_MAX_NEW": "3"}):
            for i, name in enumerate(("First", "Second", "Third")):
                self.materials()
                pdf = self.run / "paper" / "paper.pdf"
                pdf.write_bytes(b"%PDF" + bytes([65 + i]) * 60000)
                (self.run / "repo" / "main.py").write_text(f"entry = {i}\n")
                cache = self.run / "cache"
                cache.mkdir()
                (cache / "fulltext.txt").write_text(name)
                http_cache = self.runs / ".cache" / "ingest"
                http_cache.mkdir(parents=True, exist_ok=True)
                (http_cache / "response.cache").write_text(name)
                payload = self.payload(name)
                payload["commit_hash"] = chr(98 + i) * 40
                self.assertTrue(entry.stage(payload)["ok"])
                result = entry.apply()
                self.assertTrue(result["applied"])
                self.assertTrue(result["workspace_cleanup"]["ok"])
                for child in ("paper", "repo", "cache", "staged"):
                    self.assertFalse((self.run / child).exists())
                self.assertFalse(http_cache.exists())
                self.assertEqual(
                    (self.hub / entry_path(name) / "repo" / "main.py").read_text(),
                    f"entry = {i}\n",
                )
                if i == 0:
                    archive_hash = entry.digest(archive)
                self.assertEqual(entry.digest(archive), archive_hash)
            result = json.loads((self.run / "task_result.json").read_text())
            self.assertEqual(result["status"], "done")
            self.assertEqual(sum(item["applied"] for item in result["items"].values()), 3)
        self.assertEqual((archive / "cache" / "fulltext.txt").read_text(), "First")
        self.assertEqual((archive / "repo" / "main.py").read_text(), "entry = 0\n")
        saved = json.loads((archive / "applied.json").read_text())
        self.assertEqual(saved["model_name"], "First")
        self.assertTrue(saved["applied"])
        self.assertTrue((archive / "staged" / "summary.json").is_file())
        self.assertEqual(trace.read_text(), "first entry full trace\n")
        self.assertEqual(session.read_text(), "session evidence\n")
        self.assertEqual(older.read_text(), "older evidence\n")

    def test_failed_apply_keeps_materials_and_caches(self):
        self.materials()
        cache = self.run / "cache"
        cache.mkdir()
        (cache / "fulltext.txt").write_text("failure evidence")
        http_cache = self.runs / ".cache" / "ingest"
        http_cache.mkdir(parents=True)
        (http_cache / "response.cache").write_text("response")
        entry.stage(self.payload())
        before = entry.digest(self.hub)
        with patch.object(entry, "_ensure_validation_ok", side_effect=state.IngestError("bad")):
            with self.assertRaises(state.IngestError):
                entry.apply()
        self.assertEqual(entry.digest(self.hub), before)
        for child in ("paper", "repo", "cache", "staged"):
            self.assertTrue((self.run / child).is_dir())
        self.assertTrue(http_cache.is_dir())
        self.assertFalse((self.run / "first-applied").exists())

    def test_cleanup_error_preserves_applied_result(self):
        self.materials()
        entry.stage(self.payload())
        with patch.object(entry, "_cleanup_after_apply", side_effect=OSError("cleanup failed")):
            result = entry.apply()
        self.assertTrue(result["applied"])
        self.assertFalse(result["workspace_cleanup"]["ok"])
        self.assertTrue(entry.check(self.hub, only="New")["ok"])
        saved = json.loads((self.run / "task_result.json").read_text())
        self.assertTrue(saved["items"]["New"]["applied"])
        self.assertFalse(saved["items"]["New"]["workspace_cleanup"]["ok"])

    def test_cleanup_rejects_cache_symlink_escape(self):
        self.materials()
        entry.stage(self.payload())
        outside = self.root / "outside-cache"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        cache = self.runs / ".cache" / "ingest"
        cache.parent.mkdir()
        cache.symlink_to(outside, target_is_directory=True)
        result = entry.apply()
        self.assertTrue(result["applied"])
        self.assertFalse(result["workspace_cleanup"]["ok"])
        self.assertEqual((outside / "keep.txt").read_text(), "keep")
        self.assertTrue((self.run / "repo").is_dir())

    def test_run_result_depends_on_actual_apply(self):
        self.materials()
        entry.stage(self.payload())
        self.assertEqual(
            json.loads((self.run / "task_result.json").read_text())["status"], "needs_human"
        )
        entry.apply()
        result = json.loads((self.run / "task_result.json").read_text())
        self.assertEqual(result["status"], "done")
        self.assertTrue((self.run / "report.md").exists())


if __name__ == "__main__":
    unittest.main()


# ---- apply_entry CLI 机械闸 ----

import contextlib  # noqa: E402

import apply_entry  # noqa: E402


class ApplyGateTests(unittest.TestCase):
    """--confirm 与 INGEST_APPLY_AUTHORIZED=1 双条件，缺一即 needs_human。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        runs = root / "runs"
        run_dir = runs / "t1" / "ingest" / "seed"
        run_dir.mkdir(parents=True)
        for key, value in {"HUB": root / "hub", "RUNS_ROOT": runs}.items():
            p = patch.object(state, key, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"AGENT_RUN_DIR": str(run_dir)}, clear=False)
        p.start()
        self.addCleanup(p.stop)

    def _run(self, argv, env):
        buf = io.StringIO()
        with (
            patch.object(sys, "argv", ["apply_entry.py", *argv]),
            patch.dict(os.environ, env, clear=False),
            contextlib.redirect_stderr(buf),
        ):
            code = apply_entry.main()
        return code, buf.getvalue()

    def test_confirm_without_env_authorization_is_rejected(self):
        code, err = self._run(["--confirm"], {"INGEST_APPLY_AUTHORIZED": ""})
        self.assertEqual(code, 2)
        self.assertIn("INGEST_APPLY_AUTHORIZED", err)

    def test_env_without_confirm_is_rejected(self):
        code, err = self._run([], {"INGEST_APPLY_AUTHORIZED": "1"})
        self.assertEqual(code, 2)
        self.assertIn("--confirm", err)

    def test_authorized_confirm_reaches_apply(self):
        # 双闸通过后进入 apply()：无 staged → 流程级 IngestError（而非闸拒绝）
        code, err = self._run(["--confirm"], {"INGEST_APPLY_AUTHORIZED": "1"})
        self.assertEqual(code, 2)
        self.assertIn("缺少 stage_entry", err)
