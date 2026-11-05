import io
import os
import unittest
from unittest.mock import patch

from core import config
from core.ai.base import AIQuotaError
from core.pipeline import Draft, PipelineError, SettingsError
from scripts import create_article
from tests.support.config import patched_config
from tests.support.db import TempDBMixin


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = create_article.main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def draft(**overrides):
    values = dict(kind="news", slug="festa", title="Festa", excerpt="E", markdown="---\ntitle: \"Festa\"\n---\n\nCorpo\n",
                  date_iso="2026-11-05", dry_run=True)
    values.update(overrides)
    return Draft(**values)


class TestCreateArticleCli(TempDBMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.brief = os.path.join(self.temp_dir, "brief.md")
        with open(self.brief, "w", encoding="utf-8") as f:
            f.write("Un brief con è accentata")

    def test_arguments_are_passed_to_the_pipeline(self):
        with patch("scripts.create_article.create_article", return_value=draft()) as create:
            code, _, _ = run("--kind", "news", "--brief", self.brief, "--images", "a.jpg", "b.jpg", "--cover", "2",
                             "--order", "5", "--date", "05-11-2026", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(create.call_args.args, ("news", "Un brief con è accentata", ["a.jpg", "b.jpg"]))
        self.assertEqual(create.call_args.kwargs, dict(cover=2, logo=None, order=5, date="05-11-2026", dry_run=True))

    def test_dry_run_prints_the_article_with_its_site_path(self):
        with patch("scripts.create_article.create_article", return_value=draft()), patched_config(NEWS_CONTENT_DIR="src/content/news"):
            code, out, _ = run("--kind", "news", "--brief", self.brief, "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("<!-- FILE: src/content/news/2026-11-05-festa.md -->\n---\n"))
        self.assertIn("Corpo", out)

    def test_dry_run_prints_the_partner_card_too(self):
        result = draft(kind="collab", card_slug="aces", card_markdown="---\nnome: \"Aces\"\n---\n")
        with patch("scripts.create_article.create_article", return_value=result), patched_config(COLLAB_CONTENT_DIR="src/content/collaborazioni"):
            _, out, _ = run("--kind", "collab", "--brief", self.brief, "--dry-run")
        self.assertIn("<!-- FILE: src/content/collaborazioni/aces.md -->\n---\nnome: \"Aces\"", out)

    def test_dry_run_output_file(self):
        target = os.path.join(self.temp_dir, "sub", "festa.md")
        with patch("scripts.create_article.create_article", return_value=draft()):
            code, out, _ = run("--kind", "news", "--brief", self.brief, "--dry-run", "--output", target)
        self.assertEqual(code, 0)
        self.assertIn("scritto", out)
        self.assertIn("Corpo", open(target, encoding="utf-8").read())

    def test_real_run_prints_links_and_initialises_the_db(self):
        result = draft(dry_run=False, id=7, draft_url="https://github.com/x", public_url="https://sito/news/festa/")
        with patch("scripts.create_article.create_article", return_value=result):
            code, out, _ = run("--kind", "news", "--brief", self.brief)
        self.assertEqual(code, 0)
        self.assertIn("Bozza #7 creata: Festa", out)
        self.assertIn("https://github.com/x", out)
        self.assertIn("publish_article.py 7", out)

    def test_brief_from_stdin(self):
        with patch("sys.stdin", io.StringIO("brief da stdin")), \
                patch("scripts.create_article.create_article", return_value=draft()) as create:
            run("--kind", "news", "--brief", "-", "--dry-run")
        self.assertEqual(create.call_args.args[1], "brief da stdin")

    def test_provider_option_overrides_the_setting(self):
        seen = []
        with patched_config(AI_PROVIDER="gemini"), \
                patch("scripts.create_article.create_article", side_effect=lambda *a, **k: seen.append(config.AI_PROVIDER) or draft()):
            run("--kind", "news", "--brief", self.brief, "--dry-run", "--provider", "claude")
        self.assertEqual(seen, ["claude"])

    def test_exit_codes(self):
        base = ("--kind", "news", "--brief", self.brief, "--dry-run")
        cases = [
            (SettingsError("Impostazioni mancanti: X"), 2, "Impostazioni mancanti"),
            (PipelineError("upload fallito"), 1, "Errore: upload fallito"),
            (AIQuotaError("429"), 1, "esauriti"),
        ]
        for error, expected, text in cases:
            with self.subTest(error=error), patch("scripts.create_article.create_article", side_effect=error):
                code, _, err = run(*base)
            self.assertEqual(code, expected)
            self.assertIn(text, err)

    def test_missing_brief_file_is_a_usage_error(self):
        code, _, err = run("--kind", "news", "--brief", os.path.join(self.temp_dir, "manca.md"), "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("brief", err)

    def test_output_requires_dry_run(self):
        code, _, err = run("--kind", "news", "--brief", self.brief, "--output", "x.md")
        self.assertEqual(code, 2)
        self.assertIn("--dry-run", err)

    def test_invalid_kind_exits_with_argparse_error(self):
        with self.assertRaises(SystemExit) as ctx, patch("sys.stderr", io.StringIO()):
            create_article.main(["--kind", "blog", "--brief", self.brief])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
