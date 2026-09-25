import csv
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task/ingest/scripts"))
import _state as state
import _entry as entry
import audit_scan
from hubkit import render, readers
from hubkit.schema import CSV_COLUMNS, INDEX_FILES, ENTRIES_ROOT, entry_path
from test_hubkit import row, readme


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
