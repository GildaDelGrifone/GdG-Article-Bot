import io
import unittest
from unittest.mock import patch

from core import db
from core.pipeline import PipelineError, SettingsError
from scripts import publish_article
from tests.support.db import TempDBMixin


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = publish_article.main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class TestPublishArticleCli(TempDBMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.article_id = db.insert_article("news", slug="festa", branch="news/festa", title="Festa",
                                            draft_url="https://github.com/x")

    def test_list(self):
        code, out, _ = run("--list")
        self.assertEqual(code, 0)
        self.assertIn(f"#{self.article_id}", out)
        self.assertIn("festa", out)
        self.assertIn("https://github.com/x", out)

    def test_empty_list(self):
        db.update_article(self.article_id, status="discarded")
        self.assertIn("Nessuna bozza", run("--list")[1])

    def test_publish_by_id_and_by_slug(self):
        for reference in (str(self.article_id), "festa"):
            with patch("scripts.publish_article.publish", return_value="https://sito/news/festa/") as publish:
                code, out, _ = run(reference)
            self.assertEqual(code, 0)
            publish.assert_called_once_with(self.article_id)
            self.assertIn("https://sito/news/festa/", out)

    def test_discard(self):
        with patch("scripts.publish_article.discard", return_value="discarded") as discard:
            code, out, _ = run(str(self.article_id), "--discard")
        self.assertEqual(code, 0)
        discard.assert_called_once_with(self.article_id)
        self.assertIn("scartata", out)

    def test_discard_of_a_published_draft(self):
        with patch("scripts.publish_article.discard", return_value="published"):
            _, out, _ = run(str(self.article_id), "--discard")
        self.assertIn("già stata pubblicata", out)

    def test_unknown_draft(self):
        code, _, err = run("nessuna")
        self.assertEqual(code, 1)
        self.assertIn("non trovata", err)

    def test_missing_reference(self):
        code, _, err = run()
        self.assertEqual(code, 2)
        self.assertIn("--list", err)

    def test_pipeline_errors(self):
        with patch("scripts.publish_article.publish", side_effect=PipelineError("merge fallito")):
            code, _, err = run("festa")
        self.assertEqual((code, "merge fallito" in err), (1, True))
        with patch("scripts.publish_article.publish", side_effect=SettingsError("Impostazioni mancanti: X")):
            self.assertEqual(run("festa")[0], 2)


if __name__ == "__main__":
    unittest.main()
