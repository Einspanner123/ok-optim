import unittest
from hubkit import render
from hubkit.schema import CSV_COLUMNS, entry_name


def row(name="Seed"):
    return dict(zip(CSV_COLUMNS, [name, f"Single cell {name} paper", "2025", "arXiv",
        f"https://arxiv.org/abs/{name}", f"https://github.com/example/{name}", "1", "PyTorch", "MIT", "a" * 40]))


def readme(item, prefix=""):
    return ("# Hub\n![Models](https://img.shields.io/badge/Models-1-brightgreen)\n\n"
            + render.render_bullet(item, prefix)
            + "\n## 📊 Models\n\n| Model | Year | Venue | Framework | Repository |\n"
            + "| --- | --- | --- | --- | --- |\n" + render.render_table_row(item))


class HubkitTests(unittest.TestCase):
    def test_upsert_replaces_without_changing_count(self):
        before = row()
        updated = {**before, "paper_title": "Updated single cell paper"}
        result = render.upsert_readme(readme(before), "Seed", render.render_bullet(updated, ""), render.render_table_row(updated))
        self.assertEqual(result.count("* **(Seed)"), 1)
        self.assertIn("Models-1-brightgreen", result)
        self.assertIn("Updated single cell paper", result)
        self.assertNotIn(before["paper_title"], result)

    def test_append_increments_count(self):
        result = render.upsert_readme(readme(row()), "New", render.render_bullet(row("New"), ""), render.render_table_row(row("New")))
        self.assertIn("Models-2-brightgreen", result)
        self.assertEqual(result.count("* **("), 2)

    def test_names_cannot_be_paths(self):
        for value in ["../escape", "/tmp/escape", "x/y", "x\\y", ".."]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                entry_name(value)


if __name__ == "__main__":
    unittest.main()
