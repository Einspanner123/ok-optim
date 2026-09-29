"""无状态 ingest 流程测试：run 目录材料 → stage → apply → run 结果契约。"""

import csv
import io
import json
import os
import sys
import tempfile
import zipfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "task/ingest/scripts"))
import _state as state
import _entry as entry
import audit_scan
import acquire_repo
from hubkit import render, readers
from hubkit.schema import CSV_COLUMNS, INDEX_FILES, ENTRIES_ROOT, entry_path
from _fixtures import row, readme


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
        p = patch.dict(os.environ, {
            "AGENT_RUN_ID": "t1",
            "AGENT_RUN_DIR": str(self.run),
            "AGENT_SLUG": "seed",
            "INGEST_MAX_NEW": "1",
        }, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.seed = row()
        self.write_entry(self.seed)
        (self.hub / "README.md").write_text(readme(self.seed, "single_cell_models/"), encoding="utf-8")
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
        (root / "README.md").write_text(render.render_model_readme(item["model_name"], item, "official", "repository cloned at commit " + item["commit_hash"]), encoding="utf-8")

    def payload(self, name="New"):
        item = row(name)
        if name != "Seed":
            item["commit_hash"] = "b" * 40
        return {**item, "verdict": "official",
                "code_availability": "https://github.com/example/" + name}

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
        with patch.object(render, "entry_files", side_effect=AssertionError("apply must not render")):
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
        self.assertEqual((self.hub / entry_path("Seed") / "repo/main.py").read_text(), "candidate = 2\n")
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
        with patch.object(acquire_repo, "gh_api", side_effect=[meta, head]), \
             patch.object(acquire_repo, "http_get_stream", return_value=blob.getvalue()):
            result = acquire_repo._github_archive(
                "https://github.com/example/repo", target)
        self.assertEqual(result["commit"], "a" * 40)
        self.assertEqual(result["files"], 1)
        self.assertEqual((target / "main.py").read_text(), "answer = 42\n")
        self.assertFalse((target / "weights/model.bin").exists())
        self.assertFalse((target / "escape.txt").exists())
        with patch.object(acquire_repo, "_run_git", side_effect=__import__("subprocess").CalledProcessError(1, "git")), \
             patch.object(acquire_repo, "_github_archive", return_value=result) as fallback:
            self.assertEqual(acquire_repo._github(
                "https://github.com/example/repo", target), result)
            fallback.assert_called_once()

    def test_acquire_repo_reuses_empty_placeholder_only(self):
        repo = self.run / "repo"
        repo.mkdir(parents=True)
        saved = repo / "main.py"
        saved.write_text("old = 1\n")
        self.assertNotEqual(saved.stat().st_size, 0)
        url = "https://github.com/example/New"
        with patch.object(acquire_repo, "load_dotenv"), \
             patch.object(sys, "argv", ["acquire_repo.py", url]), \
             patch.object(acquire_repo, "_github") as github:
            self.assertEqual(acquire_repo.main(), 2)
            github.assert_not_called()
            saved.unlink()
            github.side_effect = lambda repo_url, dest: (
                (dest / "main.py").write_text("new = 1\n"),
                {"channel": "github", "commit": "a" * 40, "files": 1},
            )[1]
            self.assertEqual(acquire_repo.main(), 0)
            self.assertEqual((repo / "main.py").read_text(), "new = 1\n")

    def test_run_result_depends_on_actual_apply(self):
        self.materials()
        entry.stage(self.payload())
        self.assertEqual(json.loads((self.run / "task_result.json").read_text())["status"], "needs_human")
        entry.apply()
        result = json.loads((self.run / "task_result.json").read_text())
        self.assertEqual(result["status"], "done")
        self.assertTrue((self.run / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
