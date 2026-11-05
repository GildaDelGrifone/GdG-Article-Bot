import os
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from core import db, pipeline
from core.ai.base import AIQuotaError
from tests.core.test_validate import collab, news
from tests.support.config import patched_config, site_config
from tests.support.db import TempDBMixin
from tests.support.images import image_file

DRAFT_URL = "https://github.com/o/r/blob/news/x/src/content/news/2026-11-05-x.md"


def fake_upload(path, prefix, stem):
    ext = os.path.splitext(path)[1]
    key = f"{prefix}/{stem}_abcd1234{ext}"
    return {"id": key, "source_url": f"https://pub.r2.dev/{key}"}


def provider_returning(*results):
    provider = MagicMock()
    provider.generate.side_effect = list(results)
    return provider


class PipelineTestCase(TempDBMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.paths = [image_file(self.temp_dir, f"foto{n}.jpg", 100 + n, 80) for n in range(1, 4)]
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(site_config(GEMINI_API_KEY="g", ANTHROPIC_API_KEY="a", AI_PROVIDER="gemini"))
        self.upload = stack.enter_context(patch("core.pipeline.storage.upload_media", side_effect=fake_upload))
        self.create_draft = stack.enter_context(
            patch("core.pipeline.content.create_draft", return_value=(DRAFT_URL, "news/x")))
        self.update_draft = stack.enter_context(
            patch("core.pipeline.content.update_draft", return_value=DRAFT_URL))
        self.publish_draft = stack.enter_context(patch("core.pipeline.content.publish_draft", return_value=True))
        self.discard_draft = stack.enter_context(patch("core.pipeline.content.discard_draft", return_value=True))
        self.news_names = stack.enter_context(patch("core.pipeline.content.existing_news_names", return_value=[]))
        self.card_names = stack.enter_context(patch("core.pipeline.content.existing_card_names", return_value=[]))

    def create(self, provider, kind="news", brief="Il torneo è andato benissimo.", **kwargs):
        kwargs.setdefault("date", "05-11-2026")
        return pipeline.create_article(kind, brief, self.paths, provider=provider, **kwargs)


class TestCreateNews(PipelineTestCase):
    def test_happy_path(self):
        provider = provider_returning(news())
        draft = self.create(provider)

        self.assertEqual((draft.kind, draft.slug, draft.title), ("news", "torneo-di-primavera", "Torneo di primavera"))
        self.assertEqual((draft.branch, draft.draft_url), ("news/x", DRAFT_URL))
        self.assertEqual(draft.public_url, "https://example.org/news/torneo-di-primavera/")
        self.assertFalse(draft.dry_run)

        uploads = [(c.args[1], c.args[2]) for c in self.upload.call_args_list]
        self.assertEqual(uploads, [("news/torneo-di-primavera", f"img_{n}") for n in (1, 2, 3)])

        kind, slug, date_iso, markdown, card, card_slug = self.create_draft.call_args.args
        self.assertEqual((kind, slug, date_iso, card, card_slug), ("news", "torneo-di-primavera", "2026-11-05", None, None))
        self.assertIn('date: 2026-11-05\ncover: "https://pub.r2.dev/news/torneo-di-primavera/img_1_abcd1234.jpg"', markdown)
        self.assertIn("](https://pub.r2.dev/news/torneo-di-primavera/img_2_abcd1234.jpg)", markdown)
        self.assertNotIn("img:", markdown)
        self.assertEqual(markdown, draft.markdown)

    def test_database_row_and_originals(self):
        draft = self.create(provider_returning(news()), chat_id=5, message_id=6)
        row = db.get_article(draft.id)
        self.assertEqual((row["status"], row["slug"], row["branch"], row["date_iso"]), ("draft", "torneo-di-primavera", "news/x", "2026-11-05"))
        self.assertEqual(len(row["image_keys"]), 3)
        self.assertEqual((row["chat_id"], row["message_id"], row["cover_index"]), (5, 6, 1))
        self.assertEqual(row["brief"], "Il torneo è andato benissimo.")
        self.assertEqual(len(row["image_paths"]), 3)
        self.assertTrue(all(os.path.isfile(p) for p in row["image_paths"]))
        self.assertTrue(row["image_paths"][0].startswith(os.path.join(self.temp_dir, "tmp", str(draft.id))))

    def test_markers_in_the_brief_choose_the_cover(self):
        provider = provider_returning(news(cover_index=1))
        draft = self.create(provider, brief="Testo\ncopertina: 3")
        prompt = provider.generate.call_args.args[1]
        self.assertIn("image 3 as the cover", prompt)
        self.assertNotIn("copertina: 3", prompt)
        self.assertIn('cover: "https://pub.r2.dev/news/torneo-di-primavera/img_3_abcd1234.jpg"', draft.markdown)
        self.assertEqual(db.get_article(draft.id)["cover_override"], 3)

    def test_explicit_argument_beats_the_marker(self):
        provider = provider_returning(news())
        self.create(provider, brief="Testo\ncopertina: 3", cover=2)
        self.assertIn("image 2 as the cover", provider.generate.call_args.args[1])

    def test_cover_is_not_repeated_in_the_body(self):
        draft = self.create(provider_returning(news(cover_index=2)))
        self.assertNotIn("img_2_", draft.markdown.split("---\n", 2)[-1])

    def test_duplicate_slug_gets_a_suffix(self):
        self.news_names.return_value = ["2026-10-02-torneo-di-primavera.md"]
        draft = self.create(provider_returning(news()))
        self.assertEqual(draft.slug, "torneo-di-primavera-2")

    def test_vision_copies_are_numbered_images(self):
        provider = provider_returning(news())
        self.create(provider)
        images = provider.generate.call_args.args[2]
        self.assertEqual(len(images), 3)
        self.assertEqual(images[0][1], "image/jpeg")

    def test_article_without_images(self):
        result = news(cover_index=None, body=news()["body"].replace("![Giocatori al tavolo](img:2)\n_Il tavolo principale_\n\n", ""))
        draft = pipeline.create_article("news", "Solo testo", [], provider=provider_returning(result), date="05-11-2026")
        self.assertNotIn("cover:", draft.markdown)
        self.upload.assert_not_called()

    def test_date_defaults_to_today(self):
        with patch("core.pipeline.date_type") as today:
            today.today.return_value.isoformat.return_value = "2030-01-02"
            self.create(provider_returning(news()), date=None)
        self.assertEqual(self.create_draft.call_args.args[2], "2030-01-02")


class TestCreateCollab(PipelineTestCase):
    def test_happy_path(self):
        self.create_draft.return_value = (DRAFT_URL, "collaborazione/collaborazione-con-aces-games")
        draft = self.create(provider_returning(collab()), kind="collab", brief="Nuovo partner\nordine: 7")

        self.assertEqual(draft.slug, "collaborazione-con-aces-games")
        self.assertEqual(draft.card_slug, "aces-games")
        uploads = [(c.args[1], c.args[2]) for c in self.upload.call_args_list]
        self.assertEqual(uploads[:3], [("news/collaborazione-con-aces-games", f"img_{n}") for n in (1, 2, 3)])
        self.assertEqual(uploads[3], ("collaborazioni/aces-games", "logo"))
        self.assertEqual(self.upload.call_args_list[3].args[0], self.paths[2])  # il logo è l'immagine 3

        kind, slug, _, _, card, card_slug = self.create_draft.call_args.args
        self.assertEqual((kind, card_slug), ("collab", "aces-games"))
        self.assertEqual(
            card,
            '---\nnome: "Aces Games"\nlogo: "https://pub.r2.dev/collaborazioni/aces-games/logo_abcd1234.jpg"\n'
            'url: "https://example.org/news/collaborazione-con-aces-games/"\ndescrizione: "Casa editrice italiana."\nordine: 7\n---\n',
        )
        self.assertNotIn("acesgames.it", card)  # il sito del partner sta nell'articolo, non nella scheda
        row = db.get_article(draft.id)
        self.assertEqual((row["kind"], row["card_slug"], row["logo_index"], row["card_order"]), ("collab", "aces-games", 3, 7))
        self.assertTrue(row["logo_key"].startswith("collaborazioni/aces-games/logo_"))

    def test_logo_marker(self):
        provider = provider_returning(collab())
        self.create(provider, kind="collab", brief="Partner\nlogo: 3")
        self.assertIn("image 3 as the partner's logo", provider.generate.call_args.args[1])

    def test_card_and_article_slugs_are_made_unique(self):
        self.news_names.return_value = ["2026-10-02-collaborazione-con-aces-games.md"]
        self.card_names.return_value = ["aces-games.md"]
        draft = self.create(provider_returning(collab()), kind="collab")
        self.assertEqual((draft.slug, draft.card_slug), ("collaborazione-con-aces-games-2", "aces-games-2"))

    def test_needs_images(self):
        with self.assertRaisesRegex(pipeline.PipelineError, "logo"):
            pipeline.create_article("collab", "Partner", [], provider=provider_returning(collab()))

    def test_logo_upload_failure_aborts_before_the_commit(self):
        self.upload.side_effect = [fake_upload(p, "x", "i") for p in self.paths] + [None]
        with self.assertRaisesRegex(pipeline.PipelineError, "logo"):
            self.create(provider_returning(collab()), kind="collab")
        self.create_draft.assert_not_called()


class TestCreateFailures(PipelineTestCase):
    def test_upload_failure_aborts_before_any_commit(self):
        self.upload.side_effect = [fake_upload(self.paths[0], "x", "i"), None]
        with self.assertRaisesRegex(pipeline.PipelineError, "immagine 2"):
            self.create(provider_returning(news()))
        self.create_draft.assert_not_called()
        self.assertEqual(db.list_drafts(), [])

    def test_failed_draft_leaves_no_row(self):
        self.create_draft.return_value = (None, None)
        with self.assertRaisesRegex(pipeline.PipelineError, "GitHub"):
            self.create(provider_returning(news()))
        self.assertEqual(db.list_drafts(), [])

    def test_ai_never_valid(self):
        bad = news(body="Solo testo")
        with self.assertRaisesRegex(pipeline.PipelineError, "3 tentativi"):
            self.create(provider_returning(bad, bad, bad))
        self.upload.assert_not_called()

    def test_quota_error_propagates(self):
        with self.assertRaises(AIQuotaError):
            self.create(provider_returning(AIQuotaError("crediti")))

    def test_github_listing_error_stops_a_real_run(self):
        self.news_names.side_effect = RuntimeError("rete")
        with self.assertRaisesRegex(pipeline.PipelineError, "cartelle del sito"):
            self.create(provider_returning(news()))
        self.upload.assert_not_called()

    def test_missing_settings(self):
        with patched_config(R2_ACCOUNT_ID=None, GEMINI_API_KEY=None):
            with self.assertRaisesRegex(pipeline.PipelineError, "GEMINI_API_KEY, R2_ACCOUNT_ID"):
                self.create(provider_returning(news()))

    def test_bad_arguments(self):
        cases = [
            (dict(kind="blog"), "sconosciuto"),
            (dict(brief="   "), "vuoto"),
            (dict(brief="  \nlogo: 1"), "vuoto"),
            (dict(cover=9), "Copertina"),
            (dict(date="2026-11-05"), "Data non valida"),
        ]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(pipeline.PipelineError, message):
                self.create(provider_returning(news()), **kwargs)

    def test_missing_image_file(self):
        self.paths[1] = os.path.join(self.temp_dir, "non-esiste.jpg")
        with self.assertRaisesRegex(pipeline.PipelineError, "Immagine 2 non trovata"):
            self.create(provider_returning(news()))

    def test_unreadable_image_file(self):
        broken = os.path.join(self.temp_dir, "rotta.jpg")
        open(broken, "wb").write(b"non sono un'immagine")
        self.paths[0] = broken
        with self.assertRaisesRegex(pipeline.PipelineError, "non leggibile"):
            self.create(provider_returning(news()))


class TestDryRun(PipelineTestCase):
    def test_nothing_is_uploaded_committed_or_stored(self):
        draft = self.create(provider_returning(news()), dry_run=True)
        self.assertTrue(draft.dry_run)
        self.assertIsNone(draft.id)
        self.upload.assert_not_called()
        self.create_draft.assert_not_called()
        self.assertEqual(db.list_drafts(), [])
        self.assertIn("https://media.example/torneo-di-primavera/img_2.jpg", draft.markdown)
        self.assertIn('cover: "https://media.example/torneo-di-primavera/img_1.jpg"', draft.markdown)

    def test_needs_only_the_ai_key(self):
        with patched_config(R2_ACCOUNT_ID=None, ASTRO_GITHUB_TOKEN=None):
            draft = self.create(provider_returning(news()), dry_run=True)
        self.assertEqual(draft.slug, "torneo-di-primavera")

    def test_github_errors_are_tolerated(self):
        self.news_names.side_effect = RuntimeError("niente rete")
        draft = self.create(provider_returning(news()), dry_run=True)
        self.assertEqual(draft.slug, "torneo-di-primavera")

    def test_collab_dry_run_returns_the_card(self):
        draft = self.create(provider_returning(collab()), kind="collab", dry_run=True)
        self.assertEqual(draft.card_slug, "aces-games")
        self.assertIn('logo: "https://media.example/aces-games/logo.png"', draft.card_markdown)


class TestRevise(PipelineTestCase):
    def make(self, kind="news", result=None):
        result = result or (news() if kind == "news" else collab())
        return self.create(provider_returning(result), kind=kind)

    def test_revision_keeps_branch_slug_and_images(self):
        draft = self.make()
        self.upload.reset_mock()
        updated = news(title="Titolo nuovo", body=news()["body"].replace("bellissima", "splendida"))
        provider = provider_returning(updated)

        revised = pipeline.revise_article(draft.id, "più entusiasmo", provider=provider)

        self.upload.assert_not_called()
        self.assertEqual(revised.slug, "torneo-di-primavera")
        self.assertEqual(revised.title, "Titolo nuovo")
        args = self.update_draft.call_args.args
        self.assertEqual(args[:5], ("news", "torneo-di-primavera", "2026-11-05", "news/x", revised.markdown))
        self.assertIn("splendida", revised.markdown)
        self.assertIn("img_2_abcd1234.jpg", revised.markdown)  # stesso upload di prima
        prompt = provider.generate.call_args.args[1]
        self.assertIn("più entusiasmo", prompt)
        self.assertIn("PREVIOUS BODY", prompt)
        row = db.get_article(draft.id)
        self.assertEqual(row["title"], "Titolo nuovo")
        self.assertIn("splendida", row["last_body"])

    def test_revision_reuses_the_admin_choices(self):
        draft = pipeline.create_article("news", "Testo\ncopertina: 3", self.paths, provider=provider_returning(news()),
                                        date="05-11-2026")
        provider = provider_returning(news(cover_index=1))
        revised = pipeline.revise_article(draft.id, "ok", provider=provider)
        self.assertIn("image 3 as the cover", provider.generate.call_args.args[1])
        self.assertIn('cover: "https://pub.r2.dev/news/torneo-di-primavera/img_3_abcd1234.jpg"', revised.markdown)

    def test_unchanged_logo_is_not_uploaded_again(self):
        draft = self.make("collab")
        self.upload.reset_mock()
        pipeline.revise_article(draft.id, "ok", provider=provider_returning(collab()))
        self.upload.assert_not_called()

    def test_a_different_logo_is_uploaded_to_the_card_folder(self):
        draft = self.make("collab")
        self.upload.reset_mock()
        other = collab()
        other["partner"]["logo_index"] = 2
        other["body"] = other["body"].replace("![Il manuale](img:2)\n\n", "")
        pipeline.revise_article(draft.id, "il logo è la 2", provider=provider_returning(other))
        folder, stem = self.upload.call_args.args[1:]
        self.assertEqual((folder, stem), ("collaborazioni/aces-games", "logo"))
        self.assertEqual(db.get_article(draft.id)["logo_index"], 2)

    def test_errors(self):
        draft = self.make()
        with self.assertRaisesRegex(pipeline.PipelineError, "indicazioni"):
            pipeline.revise_article(draft.id, "  ", provider=provider_returning(news()))
        with self.assertRaisesRegex(pipeline.PipelineError, "non trovata"):
            pipeline.revise_article(999, "x", provider=provider_returning(news()))
        for path in db.get_article(draft.id)["image_paths"]:
            os.remove(path)
        with self.assertRaisesRegex(pipeline.PipelineError, "non sono più disponibili"):
            pipeline.revise_article(draft.id, "x", provider=provider_returning(news()))

    def test_github_failure(self):
        draft = self.make()
        self.update_draft.return_value = None
        with self.assertRaisesRegex(pipeline.PipelineError, "Aggiornamento"):
            pipeline.revise_article(draft.id, "x", provider=provider_returning(news()))
        self.assertEqual(db.get_article(draft.id)["title"], "Torneo di primavera")


class TestPublishAndDiscard(PipelineTestCase):
    def make(self):
        return self.create(provider_returning(news()))

    def test_publish(self):
        draft = self.make()
        url = pipeline.publish(draft.id)
        self.assertEqual(url, "https://example.org/news/torneo-di-primavera/")
        self.publish_draft.assert_called_once_with("news/x")
        self.assertEqual(db.get_article(draft.id)["status"], "published")
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "tmp", str(draft.id))))

    def test_publish_failure_keeps_the_draft(self):
        draft = self.make()
        self.publish_draft.return_value = False
        with self.assertRaisesRegex(pipeline.PipelineError, "Pubblicazione fallita"):
            pipeline.publish(draft.id)
        self.assertEqual(db.get_article(draft.id)["status"], "draft")
        self.assertTrue(os.path.isdir(os.path.join(self.temp_dir, "tmp", str(draft.id))))

    def test_cannot_publish_twice(self):
        draft = self.make()
        pipeline.publish(draft.id)
        with self.assertRaisesRegex(pipeline.PipelineError, "già pubblicata"):
            pipeline.publish(draft.id)
        with self.assertRaisesRegex(pipeline.PipelineError, "già pubblicata"):
            pipeline.discard(draft.id)

    def test_discard(self):
        draft = self.make()
        self.assertEqual(pipeline.discard(draft.id), "discarded")
        self.assertEqual(db.get_article(draft.id)["status"], "discarded")
        with self.assertRaisesRegex(pipeline.PipelineError, "scartata"):
            pipeline.publish(draft.id)

    def test_discard_of_an_already_published_branch(self):
        draft = self.make()
        self.discard_draft.return_value = "published"
        self.assertEqual(pipeline.discard(draft.id), "published")
        self.assertEqual(db.get_article(draft.id)["status"], "published")

    def test_discard_failure(self):
        draft = self.make()
        self.discard_draft.return_value = False
        with self.assertRaisesRegex(pipeline.PipelineError, "Eliminazione"):
            pipeline.discard(draft.id)
        self.assertEqual(db.get_article(draft.id)["status"], "draft")

    def test_unknown_draft(self):
        with self.assertRaisesRegex(pipeline.PipelineError, "non trovata"):
            pipeline.publish(404)


if __name__ == "__main__":
    unittest.main()


class TestCreateCard(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.logo = image_file(self.temp_dir, "logo.png", 300, 300)
        self.create_card_draft = patch(
            "core.pipeline.content.create_card_draft", return_value=(DRAFT_URL, "scheda/aces-games"))
        self.create_card_draft = self.create_card_draft.start()
        self.addCleanup(patch.stopall)
        self.news_names.return_value = ["2026-10-02-collaborazione-con-aces-games.md"]  # l'articolo a cui punta la scheda

    def card(self, **kwargs):
        kwargs.setdefault("descrizione", "Casa editrice italiana di giochi di ruolo.")
        kwargs.setdefault("article", "collaborazione-con-aces-games")
        return pipeline.create_card("Aces Games", self.logo, **kwargs)

    def test_happy_path_uploads_the_logo_and_creates_the_draft(self):
        draft = self.card(order=10)
        self.assertEqual((draft.kind, draft.slug, draft.card_slug, draft.title), ("card", "aces-games", "aces-games", "Aces Games"))
        self.assertEqual((draft.branch, draft.draft_url), ("scheda/aces-games", DRAFT_URL))
        self.assertEqual(draft.public_url, "https://example.org/collaborazioni/")
        self.upload.assert_called_once_with(self.logo, "collaborazioni/aces-games", "logo")
        slug, markdown = self.create_card_draft.call_args.args
        self.assertEqual(slug, "aces-games")
        self.assertIn('nome: "Aces Games"\nlogo: "https://pub.r2.dev/collaborazioni/aces-games/logo_abcd1234.png"\n'
                      'url: "https://example.org/news/collaborazione-con-aces-games/"\n'
                      'descrizione: "Casa editrice italiana di giochi di ruolo."\nordine: 10',
                      markdown)
        self.create_draft.assert_not_called()

    def test_database_row(self):
        draft = self.card()
        row = db.get_article(draft.id)
        self.assertEqual((row["kind"], row["status"], row["slug"], row["card_slug"], row["branch"]),
                         ("card", "draft", "aces-games", "aces-games", "scheda/aces-games"))
        self.assertEqual((row["card_order"], row["image_keys"], row["image_paths"]), (100, [], []))
        self.assertEqual(row["logo_key"], "collaborazioni/aces-games/logo_abcd1234.png")

    def test_card_is_published_and_discarded_like_a_draft(self):
        draft = self.card()
        self.assertEqual(pipeline.publish(draft.id), "https://example.org/collaborazioni/")
        self.publish_draft.assert_called_once_with("scheda/aces-games")
        self.assertEqual(db.get_article(draft.id)["status"], "published")

    def test_dry_run_uploads_and_commits_nothing(self):
        with patched_config(GEMINI_API_KEY=None, R2_ACCOUNT_ID=None):
            draft = self.card(dry_run=True)
        self.assertTrue(draft.dry_run)
        self.assertIn('logo: "https://media.example/aces-games/logo.png"', draft.card_markdown)
        self.upload.assert_not_called()
        self.create_card_draft.assert_not_called()

    def test_card_slug_is_made_unique(self):
        self.card_names.return_value = ["aces-games.md"]
        self.assertEqual(self.card().slug, "aces-games-2")

    def test_without_an_article_the_card_has_no_link(self):
        draft = self.card(article=None)
        self.assertNotIn("url:", draft.card_markdown)

    def test_article_date_prefix_is_ignored_when_looking_for_the_slug(self):
        self.news_names.return_value = ["2017-01-18-altro.md", "2026-10-02-collaborazione-con-aces-games.md"]
        self.assertIn("/news/collaborazione-con-aces-games/", self.card().card_markdown)

    def test_unknown_article_creates_nothing(self):
        self.news_names.return_value = ["2026-10-02-altro.md"]
        with self.assertRaisesRegex(pipeline.PipelineError, "Articolo 'collaborazione-con-aces-games' non trovato"):
            self.card()
        with self.assertRaisesRegex(pipeline.PipelineError, "non trovato"):
            self.card(dry_run=True)
        self.upload.assert_not_called()
        self.assertEqual(db.list_drafts(), [])

    def test_dry_run_tolerates_an_unreadable_site_listing(self):
        self.news_names.side_effect = RuntimeError("rete")
        self.card_names.side_effect = RuntimeError("rete")
        draft = self.card(dry_run=True)
        self.assertIn("/news/collaborazione-con-aces-games/", draft.card_markdown)
        with self.assertRaisesRegex(pipeline.PipelineError, "cartelle del sito"):
            self.card()

    def test_failed_upload_creates_nothing(self):
        self.upload.side_effect = None
        self.upload.return_value = None
        with self.assertRaisesRegex(pipeline.PipelineError, "logo"):
            self.card()
        self.create_card_draft.assert_not_called()
        self.assertEqual(db.list_drafts(), [])

    def test_failed_github_draft(self):
        self.create_card_draft.return_value = (None, None)
        with self.assertRaisesRegex(pipeline.PipelineError, "GitHub"):
            self.card()
        self.assertEqual(db.list_drafts(), [])

    def test_bad_arguments(self):
        cases = [
            (dict(descrizione="x" * 201), "200 caratteri"),
            (dict(descrizione="  "), "descrizione"),
        ]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(pipeline.PipelineError, message):
                self.card(**kwargs)
        with self.assertRaisesRegex(pipeline.PipelineError, "nome"):
            pipeline.create_card(" ", self.logo, "d")
        with self.assertRaisesRegex(pipeline.PipelineError, "Logo non trovato"):
            pipeline.create_card("Aces", os.path.join(self.temp_dir, "no.png"), "d")

    def test_missing_settings_ignore_the_ai_key(self):
        with patched_config(GEMINI_API_KEY=None, R2_ACCOUNT_ID=None):
            with self.assertRaisesRegex(pipeline.SettingsError, "^Impostazioni mancanti: R2_ACCOUNT_ID$"):
                self.card()

    def test_a_card_cannot_be_revised(self):
        draft = self.card()
        with self.assertRaisesRegex(pipeline.PipelineError, "non si rigenera"):
            pipeline.revise_article(draft.id, "più breve")
