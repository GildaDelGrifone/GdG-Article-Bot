import io
import os
import unittest
from unittest.mock import patch

from core.pipeline import Draft, PipelineError, SettingsError
from scripts import create_card
from tests.support.config import patched_config
from tests.support.db import TempDBMixin

ARGS = ("--name", "Aces Games", "--logo", "logo.png", "--description", "Casa editrice.")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = create_card.main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def draft(**overrides):
    values = dict(kind="card", slug="aces-games", title="Aces Games", excerpt="Casa editrice.", markdown="",
                  card_slug="aces-games", card_markdown="---\nnome: \"Aces Games\"\n---\n", dry_run=True)
    values.update(overrides)
    return Draft(**values)


class TestCreateCardCli(TempDBMixin, unittest.TestCase):
    def test_arguments_are_passed_to_the_pipeline(self):
        with patch("scripts.create_card.create_card", return_value=draft()) as create:
            code, _, _ = run(*ARGS, "--article", "collaborazione-con-aces-games", "--order", "10", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(create.call_args.args, ("Aces Games", "logo.png", "Casa editrice."))
        self.assertEqual(create.call_args.kwargs, dict(article="collaborazione-con-aces-games", order=10, dry_run=True))

    def test_dry_run_prints_the_card_with_its_site_path(self):
        with patch("scripts.create_card.create_card", return_value=draft()), \
                patched_config(COLLAB_CONTENT_DIR="src/content/collaborazioni"):
            code, out, _ = run(*ARGS, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(out, "<!-- FILE: src/content/collaborazioni/aces-games.md -->\n---\nnome: \"Aces Games\"\n---\n")

    def test_dry_run_output_file(self):
        target = os.path.join(self.temp_dir, "sub", "card.md")
        with patch("scripts.create_card.create_card", return_value=draft()):
            code, out, _ = run(*ARGS, "--dry-run", "--output", target)
        self.assertEqual(code, 0)
        self.assertIn("scritto", out)
        with open(target, encoding="utf-8") as f:
            self.assertIn("nome:", f.read())

    def test_real_run_prints_the_links(self):
        real = draft(id=7, dry_run=False, draft_url="https://gh/draft", public_url="https://site/collaborazioni/")
        with patch("scripts.create_card.create_card", return_value=real):
            code, out, _ = run(*ARGS)
        self.assertEqual(code, 0)
        self.assertIn("Bozza #7 creata", out)
        self.assertIn("https://gh/draft", out)
        self.assertIn("publish_article.py 7", out)

    def test_output_without_dry_run_is_rejected(self):
        code, _, err = run(*ARGS, "--output", "x.md")
        self.assertEqual(code, 2)
        self.assertIn("--dry-run", err)

    def test_errors_map_to_exit_codes(self):
        with patch("scripts.create_card.create_card", side_effect=SettingsError("Impostazioni mancanti: R2_BUCKET")):
            code, _, err = run(*ARGS)
        self.assertEqual((code, err.strip()), (2, "Impostazioni mancanti: R2_BUCKET"))
        with patch("scripts.create_card.create_card", side_effect=PipelineError("Logo non trovato")):
            code, _, err = run(*ARGS)
        self.assertEqual((code, err.strip()), (1, "Errore: Logo non trovato"))
