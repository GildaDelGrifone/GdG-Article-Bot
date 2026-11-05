import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import db
from core.agent import tools as agent_tools
from core.pipeline import PipelineError
from tests.support.config import patched_config
from tests.support.db import TempDBMixin
from tests.support.images import image_file


def fake_draft(**kw):
    base = dict(id=5, kind="news", title="Titolo", excerpt="Estratto", card_slug=None, draft_url="https://gh/draft",
                public_url="https://site/news/x/")
    return SimpleNamespace(**{**base, **kw})


class ToolTestCase(TempDBMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tools = agent_tools.build_tools()
        self.context = agent_tools.AgentContext(1, os.path.join(self.temp_dir, "sess"))

    def call(self, tool, **args):
        return agent_tools.run_tool(self.tools, self.context, tool, args)

    def add_image(self, name="a.jpg"):
        return self.context.add_image(image_file(self.temp_dir, name, 40, 30))


class TestContext(ToolTestCase):
    def test_images_are_numbered_continuously_and_keep_the_extension(self):
        first = self.add_image("a.jpg")
        second = self.context.add_image(image_file(self.temp_dir, "b.png", 10, 10))
        self.assertEqual((first, second), (1, 2))
        self.assertEqual([os.path.basename(i["path"]) for i in self.context.images], ["01.jpg", "02.png"])

    def test_unknown_image_numbers_are_an_error(self):
        with self.assertRaises(PipelineError):
            self.context.image_paths([1])

    def test_spec_names_are_unique_and_there_is_no_publish_tool(self):
        names = [t.spec.name for t in self.tools]
        self.assertEqual(len(names), len(set(names)))
        for forbidden in ("publish", "discard", "publish_draft", "discard_draft"):
            self.assertNotIn(forbidden, names)


class TestCreate(ToolTestCase):
    def test_create_article_maps_image_numbers_to_files(self):
        self.add_image("a.jpg")
        self.add_image("b.jpg")
        with patch("core.agent.tools.create_article", return_value=fake_draft()) as create:
            text = self.call("create_article", kind="news", brief="B", images=[2, 1], cover=1)
        args, kwargs = create.call_args
        self.assertEqual(args[0:2], ("news", "B"))
        self.assertEqual([os.path.basename(p) for p in args[2]], ["02.jpg", "01.jpg"])
        self.assertEqual(kwargs["cover"], 1)
        self.assertIn("Bozza #5", text)
        self.assertIn("https://gh/draft", text)

    def test_invalid_kind_and_missing_images(self):
        with self.assertRaises(PipelineError):
            self.call("create_article", kind="serata", brief="B", images=[])
        with self.assertRaises(PipelineError):
            self.call("create_article", kind="news", brief="B", images=[9])

    def test_unknown_arguments_are_an_error_not_a_crash(self):
        with self.assertRaises(PipelineError):
            self.call("create_article", kind="news", brief="B", images=[], colore="rosso")

    def test_create_card(self):
        number = self.add_image()
        with patch("core.agent.tools.create_card", return_value=fake_draft(kind="card")) as create:
            self.call("create_card", name="N", logo=number, description="D", article="slug")
        self.assertEqual(create.call_args.args[0], "N")
        self.assertEqual(create.call_args.kwargs["article"], "slug")

    def test_revise_draft(self):
        with patch("core.agent.tools.revise_article", return_value=fake_draft(title="Nuovo")) as revise:
            text = self.call("revise_draft", id=5, feedback="più breve")
        revise.assert_called_once_with(5, "più breve")
        self.assertIn("Nuovo", text)

    def test_unknown_tool(self):
        with self.assertRaises(ValueError):
            self.call("rm_rf")


class TestFetchPost(ToolTestCase):
    def test_photos_become_session_images_and_text_is_labelled_as_data(self):
        photo = image_file(self.temp_dir, "p.jpg", 20, 20)
        post = {"text": "Ciao a tutti", "date_iso": "2026-05-03", "photo_files": [photo], "links": ["https://x.it"],
                "has_video": False}
        with patch("core.agent.tools.fetch_post", return_value=post):
            text = self.call("fetch_telegram_post", url="https://t.me/canale/1")
        self.assertIn("03-05-2026", text)
        self.assertIn("Immagini salvate nella sessione: 1", text)
        self.assertIn("https://x.it", text)
        self.assertIn("DATI", text)
        self.assertEqual(len(self.context.images), 1)


class TestShowAndFix(ToolTestCase):
    def pull(self, files):
        def fake_pull(article_id, folder):
            for name, text in files.items():
                with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                    f.write(text)
        return fake_pull

    def run_fix(self, files, edits):
        pushed = {}

        def fake_push(article_id, folder):
            for name in files:
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    pushed[name] = f.read()
            return "https://gh/draft"

        with patch("core.agent.tools.pull_draft", side_effect=self.pull(files)), \
                patch("core.agent.tools.push_draft", side_effect=fake_push) as push:
            text = self.call("fix_draft", id=5, edits=edits)
        return text, pushed, push

    def test_show_draft_marks_each_file(self):
        with patch("core.agent.tools.show_draft", return_value=[("src/a.md", "uno\n"), ("src/b.md", "due\n")]):
            text = self.call("show_draft", id=5)
        self.assertEqual(text, "<!-- FILE: src/a.md -->\nuno\n\n<!-- FILE: src/b.md -->\ndue\n")

    def test_fix_replaces_exactly_one_occurrence_and_pushes(self):
        files = {"articolo.md": "Un reffuso qui\nAltro testo", "scheda.md": "nome: X"}
        text, pushed, push = self.run_fix(files, [{"file": "articolo", "old": "reffuso", "new": "refuso"},
                                                  {"file": "scheda", "old": "X", "new": "Y"}])
        self.assertEqual(pushed["articolo.md"], "Un refuso qui\nAltro testo")
        self.assertEqual(pushed["scheda.md"], "nome: Y")
        push.assert_called_once()
        self.assertIn("aggiornata", text)

    def test_fix_rejects_missing_or_repeated_text_and_pushes_nothing(self):
        files = {"articolo.md": "ab ab"}
        for old, expected in (("zz", "0 volte"), ("ab", "2 volte"), ("", "0 volte")):
            with patch("core.agent.tools.pull_draft", side_effect=self.pull(files)), \
                    patch("core.agent.tools.push_draft") as push:
                with self.assertRaises(PipelineError) as ctx:
                    self.call("fix_draft", id=5, edits=[{"file": "articolo", "old": old, "new": "x"}])
            self.assertIn(expected, str(ctx.exception))
            push.assert_not_called()

    def test_fix_rejects_unknown_file_and_empty_edits(self):
        with patch("core.agent.tools.pull_draft", side_effect=self.pull({"articolo.md": "t"})), \
                patch("core.agent.tools.push_draft") as push:
            with self.assertRaises(PipelineError):
                self.call("fix_draft", id=5, edits=[{"file": "scheda", "old": "t", "new": "u"}])
            with self.assertRaises(PipelineError):
                self.call("fix_draft", id=5, edits=[])
        push.assert_not_called()

    def test_fix_leaves_no_temporary_folder_behind(self):
        created = []
        real = agent_tools.tempfile.mkdtemp

        def spy(*a, **k):
            created.append(real(*a, **k))
            return created[-1]

        with patch("core.agent.tools.tempfile.mkdtemp", side_effect=spy):
            self.run_fix({"articolo.md": "a b"}, [{"file": "articolo", "old": "a", "new": "c"}])
        self.assertTrue(created)
        self.assertFalse(any(os.path.exists(path) for path in created))


class TestDraftsAndPublication(ToolTestCase):
    def draft(self, **kw):
        return db.insert_article("news", slug="festa", branch="news/festa", title="Festa", public_url="https://s/n/festa/", **kw)

    def test_list_drafts(self):
        self.assertEqual(self.call("list_drafts"), "Nessuna bozza in attesa.")
        article_id = self.draft()
        self.assertIn(f"#{article_id} [news] Festa", self.call("list_drafts"))

    def test_request_publication_only_marks_the_draft(self):
        article_id = self.draft()
        with patch("core.pipeline.publish") as publish, patch("core.pipeline.discard") as discard:
            text = self.call("request_publication", id=article_id)
            self.call("request_publication", id=article_id)
        publish.assert_not_called()
        discard.assert_not_called()
        self.assertEqual(self.context.publish_ids, [article_id])
        self.assertEqual(db.get_article(article_id)["status"], "draft")
        self.assertIn("Non è pubblicata", text)

    def test_request_publication_refuses_missing_or_closed_drafts(self):
        closed = self.draft()
        db.update_article(closed, status="published")
        for article_id in (999, closed):
            with self.assertRaises(PipelineError):
                self.call("request_publication", id=article_id)

    def test_check_live_only_for_the_site(self):
        with patched_config(ASTRO_BASE_URL="https://example.org"):
            with patch("core.agent.tools.storage.is_reachable", return_value=True) as reachable:
                self.assertEqual(self.call("check_live", url="https://example.org/news/x/"), "risponde 200")
                reachable.assert_called_once_with("https://example.org/news/x/")
            with patch("core.agent.tools.storage.is_reachable", return_value=False):
                self.assertEqual(self.call("check_live", url="https://example.org/news/y/"), "non risponde 200")
            for url in ("https://evil.example/", "https://example.org.evil.io/x", "http://169.254.169.254/"):
                with patch("core.agent.tools.storage.is_reachable") as reachable, self.assertRaises(PipelineError):
                    self.call("check_live", url=url)
                reachable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
