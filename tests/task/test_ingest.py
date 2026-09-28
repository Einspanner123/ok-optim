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
        self.work = self.root / ".ingest"
        self.work.mkdir()
        for key, value in {"REPO_ROOT": self.root, "HUB": self.hub, "INGEST_ROOT": self.work, "LEDGER": self.work / "ledger.jsonl"}.items():
            p = patch.object(state, key, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {}, clear=True)
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

    def candidate(self, name="New"):
        item = row(name)
        if name != "Seed":
            item["commit_hash"] = "b" * 40
        slug = name.lower()
        state.update_candidate(slug, {**item, "verdict": "official", "code_availability": "https://github.com/example/" + name})
        cdir = state.candidate_dir(slug)
        (cdir / "paper").mkdir()
        # Legacy download name need not equal model_name.
        (cdir / "paper" / f"{slug}.pdf").write_bytes(b"%PDF" + b"y" * 60000)
        (cdir / "repo").mkdir()
        (cdir / "repo/main.py").write_text("candidate = 2\n")
        return slug

    def test_status_is_read_only_and_tracks_stage_freshness(self):
        slug = self.candidate()
        path = state.candidate_dir(slug) / "candidate.json"
        data = json.loads(path.read_text())
        for field in ("framework", "license", "verdict"):
            data.pop(field)
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        snapshot = entry.status_snapshot(slug)
        self.assertEqual(snapshot["phase"], "metadata_incomplete")
        self.assertEqual(snapshot["missing"], ["framework", "license", "verdict"])
        self.assertEqual(path.read_bytes(), before)
        state.update_candidate(slug, {"framework": "PyTorch", "license": "MIT",
                                      "verdict": "official"})
        self.assertEqual(entry.status_snapshot(slug)["phase"], "ready_to_stage")
        self.assertTrue(entry.stage(slug)["ok"])
        self.assertEqual(entry.status_snapshot(slug)["phase"], "staged")
        (self.hub / ".gitignore").write_text("# changed\n")
        self.assertEqual(entry.status_snapshot(slug)["phase"], "staged_stale")

    def test_old_unrelated_validation_errors_can_be_restaged(self):
        slug = self.candidate()
        self.assertTrue(entry.stage(slug)["ok"])
        path = state.candidate_dir(slug) / "staged/summary.json"
        summary = json.loads(path.read_text())
        summary["validation"] = {
            "ok": False, "error_count": 1,
            "errors": [{"model": "Seed", "message": "bad legacy PDF"}],
        }
        path.write_text(json.dumps(summary))
        self.assertEqual(entry.status_snapshot(slug)["phase"], "staged_stale")

    def test_acquire_repo_reuses_empty_placeholder_only(self):
        slug = self.candidate()
        repo = state.candidate_dir(slug) / "repo"
        saved = repo / "main.py"
        self.assertNotEqual(saved.stat().st_size, 0)
        url = "https://github.com/example/New"
        with patch.object(acquire_repo, "load_dotenv"), \
             patch.object(sys, "argv", ["acquire_repo.py", slug, url]), \
             patch.object(acquire_repo, "_github") as github:
            self.assertEqual(acquire_repo.main(), 2)
            github.assert_not_called()
            saved.unlink()
            self.assertEqual(entry.status_snapshot(slug)["phase"], "materials_missing")
            github.side_effect = lambda repo_url, dest: (
                (dest / "main.py").write_text("new = 1\n"),
                {"channel": "github", "commit": "a" * 40, "files": 1},
            )[1]
            self.assertEqual(acquire_repo.main(), 0)
            self.assertEqual((repo / "main.py").read_text(), "new = 1\n")

    def test_status_lists_every_candidate(self):
        for name in ("A", "B", "C", "D", "E", "F"):
            self.candidate(name)
        listed = entry.status_snapshot()
        self.assertEqual(len(listed["items"]), 6)
        self.assertFalse(listed["truncated"])

    def test_newer_candidate_evidence_supersedes_pending_status(self):
        slug = self.candidate()
        state.append_ledger(state.load_candidate_raw(slug), "official", "old pending")
        self.assertEqual(entry.status_snapshot(slug)["phase"], "pending")
        path = state.candidate_dir(slug) / "candidate.json"
        cand = json.loads(path.read_text())
        cand["updated_at"] = "2099-01-01T00:00:00+00:00"
        path.write_text(json.dumps(cand))
        self.assertEqual(entry.status_snapshot(slug)["phase"], "ready_to_stage")

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

    def test_stage_does_not_modify_hub_and_apply_uses_exact_files(self):
        slug = self.candidate()
        before = entry.digest(self.hub)
        result = entry.stage(slug)
        self.assertTrue(result["ok"], result)
        self.assertEqual(entry.digest(self.hub), before)
        stage = state.candidate_dir(slug) / "staged"
        expected = {rel: (stage / rel).read_bytes() for rel in INDEX_FILES}
        with patch.object(render, "entry_files", side_effect=AssertionError("apply must not render")):
            self.assertTrue(entry.apply(slug)["applied"])
        for rel, contents in expected.items():
            self.assertEqual((self.hub / rel).read_bytes(), contents)
        self.assertTrue(state.load_ledger()[-1]["applied"])
        self.assertTrue(entry.check(self.hub)["ok"])

    def test_existing_unrelated_errors_do_not_block_a_valid_new_entry(self):
        (self.hub / entry_path("Seed") / "paper/Seed.pdf").write_bytes(b"bad legacy PDF")
        (self.hub / ENTRIES_ROOT / "Orphan").mkdir()
        self.assertFalse(entry.check(self.hub)["ok"])
        slug = self.candidate()
        self.assertTrue(entry.stage(slug)["ok"])
        self.assertTrue(entry.apply(slug)["ok"])
        self.assertTrue(entry.check(self.hub, only="New")["ok"])
        self.assertFalse(entry.check(self.hub)["ok"])

    def test_audit_update_replaces_row_and_entry(self):
        slug = self.candidate("Seed")
        self.assertTrue(entry.stage(slug)["ok"])
        entry.apply(slug)
        rows, _ = readers.load_models_csv(self.hub)
        self.assertEqual(len(rows), 1)
        self.assertEqual((self.hub / entry_path("Seed") / "repo/main.py").read_text(), "candidate = 2\n")
        self.assertFalse(audit_scan.scan()["anomalies"])

    def test_candidate_change_invalidates_stage(self):
        slug = self.candidate()
        entry.stage(slug)
        state.update_candidate(slug, {"license": "Apache-2.0"})
        with self.assertRaisesRegex(state.IngestError, "重新 stage"):
            entry.apply(slug)

    def test_hub_change_invalidates_stage(self):
        slug = self.candidate()
        entry.stage(slug)
        (self.hub / ".gitignore").write_text("# changed\n")
        with self.assertRaisesRegex(state.IngestError, "重新 stage"):
            entry.apply(slug)

    def test_staged_content_change_is_rejected(self):
        slug = self.candidate()
        entry.stage(slug)
        (state.candidate_dir(slug) / "staged/README.md").write_text("tampered")
        with self.assertRaisesRegex(state.IngestError, "staged 内容已变化"):
            entry.apply(slug)

    def test_post_validation_failure_restores_existing_entry_and_indexes(self):
        slug = self.candidate("Seed")
        entry.stage(slug)
        before = entry.digest(self.hub)
        good = {"ok": True, "errors": [], "error_count": 0}
        bad = {"ok": False, "errors": [{"message": "injected failure"}], "error_count": 1}
        with patch.object(entry, "check", side_effect=[good, bad]):
            with self.assertRaises(state.IngestError):
                entry.apply(slug)
        self.assertEqual(entry.digest(self.hub), before)
        self.assertFalse(state.LEDGER.exists())

    def test_ledger_write_failure_rolls_back_new_entry(self):
        slug = self.candidate()
        entry.stage(slug)
        before = entry.digest(self.hub)
        with patch.object(state, "append_ledger", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                entry.apply(slug)
        self.assertEqual(entry.digest(self.hub), before)

    def test_record_cannot_mark_official_applied(self):
        slug = self.candidate()
        record = state.append_ledger(state.load_candidate_raw(slug), "official", "evidence")
        self.assertFalse(record["applied"])

    def test_run_result_depends_on_actual_apply(self):
        slug = self.candidate()
        run = self.root / "runs/test"
        with patch.dict(os.environ, {"AGENT_RUN_ID": "test", "AGENT_RUN_DIR": str(run), "AGENT_SLUG": "seed", "INGEST_MAX_NEW": "1"}):
            entry.stage(slug)
            self.assertEqual(json.loads((run / "task_result.json").read_text())["status"], "needs_human")
            entry.apply(slug)
            result = json.loads((run / "task_result.json").read_text())
            self.assertEqual(result["status"], "done")
            self.assertTrue((run / "report.md").exists())

    def test_symlink_candidate_escape_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.work / "candidates").mkdir()
        (self.work / "candidates/escape").symlink_to(outside)
        with self.assertRaises(state.IngestError):
            state.update_candidate("escape", {"paper_title": "x", "paper_url": "y"})


if __name__ == "__main__":
    unittest.main()
