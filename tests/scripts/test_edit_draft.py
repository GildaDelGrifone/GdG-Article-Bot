import io
import os
import unittest
from unittest.mock import patch

from core import db
from core.ai.base import AIQuotaError
from core.pipeline import Draft, PipelineError, SettingsError
from scripts import edit_draft
from tests.support.db import TempDBMixin


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = edit_draft.main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class TestEditDraftCli(TempDBMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.id = db.insert_article("news", slug="festa", branch="news/festa", title="Festa", date_iso="2026-11-05")

    def test_show_prints_each_file_with_its_site_path(self):
        files = [("src/content/news/2026-11-05-festa.md", "ARTICOLO\n"), ("src/content/collaborazioni/x.md", "SCHEDA\n")]
        with patch("scripts.edit_draft.show_draft", return_value=files) as show:
            code, out, err = run("show", str(self.id))
        self.assertEqual((code, err), (0, ""))
        show.assert_called_once_with(self.id)
        self.assertEqual(out, "<!-- FILE: src/content/news/2026-11-05-festa.md -->\nARTICOLO\n\n"
                              "<!-- FILE: src/content/collaborazioni/x.md -->\nSCHEDA\n")

    def test_draft_can_be_given_by_slug(self):
        with patch("scripts.edit_draft.show_draft", return_value=[]) as show:
            self.assertEqual(run("show", "festa")[0], 0)
        show.assert_called_once_with(self.id)

    def test_pull_uses_the_default_folder_per_draft(self):
        with patch("scripts.edit_draft.pull_draft", return_value=["drafts/1/articolo.md"]) as pull:
            code, out, _ = run("pull", "festa")
        self.assertEqual(code, 0)
        self.assertEqual(pull.call_args.args, (self.id, os.path.join("drafts", str(self.id))))
        self.assertIn("Scritto drafts/1/articolo.md", out)

    def test_push_with_a_custom_folder(self):
        with patch("scripts.edit_draft.push_draft", return_value="https://github.com/x") as push:
            code, out, _ = run("push", str(self.id), "--dir", "/tmp/mia")
        self.assertEqual(code, 0)
        self.assertEqual(push.call_args.args, (self.id, "/tmp/mia"))
        self.assertIn("aggiornata: https://github.com/x", out)

    def test_revise_passes_the_feedback(self):
        draft = Draft("news", "festa", "Festa", "E", "md", draft_url="https://github.com/x")
        with patch("scripts.edit_draft.revise_article", return_value=draft) as revise:
            code, out, _ = run("revise", str(self.id), "--feedback", "più breve")
        self.assertEqual(code, 0)
        self.assertEqual(revise.call_args.args, (self.id, "più breve"))
        self.assertIn("rigenerata", out)

    def test_revise_needs_a_feedback(self):
        with self.assertRaises(SystemExit) as cm, patch("sys.stderr", io.StringIO()):
            run("revise", str(self.id))
        self.assertEqual(cm.exception.code, 2)

    def test_unknown_draft(self):
        code, _, err = run("show", "999")
        self.assertEqual(code, 1)
        self.assertIn("non trovata", err)

    def test_pipeline_error_exits_1(self):
        with patch("scripts.edit_draft.push_draft", side_effect=PipelineError("L'articolo non è valido: x")):
            code, _, err = run("push", str(self.id))
        self.assertEqual((code, err), (1, "Errore: L'articolo non è valido: x\n"))

    def test_missing_settings_exit_2(self):
        with patch("scripts.edit_draft.show_draft", side_effect=SettingsError("Impostazioni mancanti: R2_BUCKET")):
            self.assertEqual(run("show", str(self.id))[0], 2)

    def test_quota_error_exits_1(self):
        with patch("scripts.edit_draft.revise_article", side_effect=AIQuotaError("429")):
            code, _, err = run("revise", str(self.id), "--feedback", "x")
        self.assertEqual(code, 1)
        self.assertIn("Quota", err)


if __name__ == "__main__":
    unittest.main()
