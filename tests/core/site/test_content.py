import base64
import unittest
from unittest.mock import patch

from core.site import content
from tests.core.site.test_github import calls, patched_requests, response
from tests.support.config import site_config


class TestSlugs(unittest.TestCase):
    def test_slugify_removes_accents_and_symbols(self):
        self.assertEqual(content.slugify("Festival dell'Oriente: è tornato!"), "festival-dell-oriente-e-tornato")

    def test_slugify_empty_falls_back(self):
        self.assertEqual(content.slugify("!!!"), "articolo")

    def test_slugify_is_limited_at_a_word_boundary(self):
        slug = content.slugify("parola " * 20)
        self.assertLessEqual(len(slug), 60)
        self.assertFalse(slug.endswith("-"))
        self.assertTrue(all(part == "parola" for part in slug.split("-")))

    def test_strip_date_matches_the_site_rule(self):
        self.assertEqual(content.strip_date("2026-04-festival-oriente.md"), "festival-oriente")
        self.assertEqual(content.strip_date("2026-10-28-x.md"), "x")
        self.assertEqual(content.strip_date("senza-data.md"), "senza-data")
        self.assertEqual(content.strip_date("2026-10-02-10-anni.md"), "10-anni")

    def test_unique_slug_compares_without_the_date(self):
        existing = ["2018-09-06-festa.md", "2019-03-20-festa-2.md"]
        self.assertEqual(content.unique_slug("nuova", existing), "nuova")
        self.assertEqual(content.unique_slug("festa", existing), "festa-3")

    def test_unique_slug_for_cards_uses_plain_names(self):
        self.assertEqual(content.unique_slug("aces-games", ["aces-games.md"], strip=False), "aces-games-2")
        self.assertEqual(content.unique_slug("2026-x", ["2026-x.md"], strip=False), "2026-x-2")


class TestExcerpt(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        self.assertEqual(content.make_excerpt("Ciao **mondo**"), "Ciao mondo")

    def test_images_and_links_are_flattened(self):
        text = "Vai su [il sito](https://x.it) ![alt](https://x/a.jpg) ora"
        self.assertEqual(content.make_excerpt(text), "Vai su il sito ora")

    def test_long_text_is_cut_with_ellipsis(self):
        excerpt = content.make_excerpt("parola " * 100)
        self.assertLessEqual(len(excerpt), 161)
        self.assertTrue(excerpt.endswith("…"))


class TestFrontMatter(unittest.TestCase):
    def test_news_front_matter_exact(self):
        text = content.build_news_front_matter('Titolo "x"', "2026-11-05", "https://m/c.jpg", "Estratto…")
        self.assertEqual(
            text,
            '---\ntitle: "Titolo \\"x\\""\ndate: 2026-11-05\ncover: "https://m/c.jpg"\nexcerpt: "Estratto…"\n---\n',
        )

    def test_news_front_matter_without_cover(self):
        text = content.build_news_front_matter("T", "2026-11-05", None, "E")
        self.assertNotIn("cover", text)

    def test_backslash_is_escaped(self):
        self.assertIn('title: "a\\\\b"', content.build_news_front_matter("a\\b", "2026-11-05", None, "E"))

    def test_news_markdown_joins_front_matter_and_body(self):
        text = content.build_news_markdown("---\nt: 1\n---\n", "\nCorpo\n\n")
        self.assertEqual(text, "---\nt: 1\n---\n\nCorpo\n")

    def test_partner_card_exact(self):
        text = content.build_partner_card("Aces Games", "https://m/l.png", "https://acesgames.it", "Casa editrice.")
        self.assertEqual(
            text,
            '---\nnome: "Aces Games"\nlogo: "https://m/l.png"\nurl: "https://acesgames.it"\n'
            'descrizione: "Casa editrice."\nordine: 100\n---\n',
        )

    def test_partner_card_without_url(self):
        text = content.build_partner_card("X", "https://m/l.png", None, "D", ordine=5)
        self.assertNotIn("url:", text)
        self.assertIn("ordine: 5", text)


class TestPaths(unittest.TestCase):
    def test_paths_branches_and_url(self):
        with site_config():
            self.assertEqual(content.news_path("2026-11-05", "festa"), "src/content/news/2026-11-05-festa.md")
            self.assertEqual(content.card_path("aces"), "src/content/collaborazioni/aces.md")
            self.assertEqual(content.branch_name("news", "festa"), "news/festa")
            self.assertEqual(content.branch_name("collab", "x"), "collaborazione/x")
            self.assertEqual(content.public_url("festa"), "https://example.org/news/festa/")

    def test_directory_settings_with_slashes(self):
        with site_config(NEWS_CONTENT_DIR="/src/content/news/"):
            self.assertEqual(content.news_path("2026-11-05", "a"), "src/content/news/2026-11-05-a.md")


BASE = {"object": {"sha": "abc"}}


class TestCreateDraft(unittest.TestCase):
    def test_news_draft_sequence(self):
        responses = [
            response(404),                   # branch_exists
            response(200, BASE),             # sha of main
            response(201),                   # create ref
            response(404), response(201),    # get + put news file
        ]
        with site_config(), patched_requests(*responses) as req:
            url, branch = content.create_draft("news", "festa", "2026-11-05", "# md")
        self.assertEqual(branch, "news/festa")
        self.assertEqual(url, "https://github.com/o/r/blob/news/festa/src/content/news/2026-11-05-festa.md")
        self.assertEqual([c[0] for c in calls(req)], ["GET", "GET", "POST", "GET", "PUT"])
        put = req.call_args_list[4]
        self.assertEqual(base64.b64decode(put.kwargs["json"]["content"]).decode(), "# md")
        self.assertEqual(put.kwargs["json"]["message"], "Bozza news festa")

    def test_collab_draft_commits_news_and_card(self):
        responses = [response(404), response(200, BASE), response(201),
                     response(404), response(201), response(404), response(201)]
        with site_config(), patched_requests(*responses) as req:
            url, branch = content.create_draft("collab", "collaborazione-con-x", "2026-11-05", "news", "card", "x")
        self.assertEqual(branch, "collaborazione/collaborazione-con-x")
        paths = [c[1] for c in calls(req) if c[0] == "PUT"]
        self.assertEqual(paths, ["contents/src/content/news/2026-11-05-collaborazione-con-x.md",
                                 "contents/src/content/collaborazioni/x.md"])

    def test_failed_commit_deletes_a_branch_we_created(self):
        responses = [response(404), response(200, BASE), response(201), response(404), response(422, text="bad"),
                     response(204)]
        with site_config(), patched_requests(*responses) as req:
            self.assertEqual(content.create_draft("news", "festa", "2026-11-05", "md"), (None, None))
        self.assertEqual(calls(req)[-1][0], "DELETE")

    def test_failed_commit_keeps_a_preexisting_branch(self):
        responses = [response(200, {}), response(200, BASE), response(422, text="already exists"),
                     response(404), response(422, text="bad")]
        with site_config(), patched_requests(*responses) as req:
            self.assertEqual(content.create_draft("news", "festa", "2026-11-05", "md"), (None, None))
        self.assertNotIn("DELETE", [c[0] for c in calls(req)])

    def test_no_credentials(self):
        with site_config(ASTRO_GITHUB_TOKEN=None), patch("core.site.client.requests.request") as req:
            self.assertEqual(content.create_draft("news", "festa", "2026-11-05", "md"), (None, None))
        req.assert_not_called()

    def test_network_error(self):
        with site_config(), patch("core.site.client.requests.request", side_effect=ConnectionError("x")):
            self.assertEqual(content.create_draft("news", "festa", "2026-11-05", "md"), (None, None))


class TestUpdatePublishDiscard(unittest.TestCase):
    def test_update_overwrites_on_the_same_branch(self):
        with site_config(), patched_requests(response(200, {}), response(200, {"sha": "old"}), response(200)) as req:
            url = content.update_draft("news", "festa", "2026-11-05", "news/festa", "new md")
        self.assertTrue(url.endswith("2026-11-05-festa.md"))
        self.assertEqual(req.call_args_list[2].kwargs["json"]["sha"], "old")

    def test_update_of_a_gone_branch_fails(self):
        with site_config(), patched_requests(response(404)):
            self.assertIsNone(content.update_draft("news", "festa", "2026-11-05", "news/festa", "md"))

    def test_publish_merges_then_deletes(self):
        with site_config(), patched_requests(response(201), response(204)) as req:
            self.assertTrue(content.publish_draft("news/festa"))
        self.assertEqual([c[0] for c in calls(req)], ["POST", "DELETE"])
        self.assertEqual(req.call_args_list[0].kwargs["json"]["commit_message"], "Pubblica news/festa")

    def test_publish_counts_even_if_branch_delete_fails(self):
        with site_config(), patched_requests(response(204), response(422, text="x")):
            self.assertTrue(content.publish_draft("news/festa"))

    def test_publish_conflict(self):
        with site_config(), patched_requests(response(409, text="conflict")) as req:
            self.assertFalse(content.publish_draft("news/festa"))
        self.assertEqual(req.call_count, 1)

    def test_discard_deletes(self):
        with site_config(), patched_requests(response(200, {}), response(204)) as req:
            self.assertIs(content.discard_draft("news/festa"), True)
        self.assertEqual([c[0] for c in calls(req)], ["GET", "DELETE"])

    def test_discard_of_a_published_article(self):
        with site_config(), patched_requests(response(404)):
            self.assertEqual(content.discard_draft("news/festa"), "published")

    def test_discard_failure(self):
        with site_config(), patched_requests(response(200, {}), response(500, text="x")):
            self.assertIs(content.discard_draft("news/festa"), False)


class TestExistingNames(unittest.TestCase):
    def test_lists_use_the_configured_folders_and_branch(self):
        with site_config(), patch("core.site.content.github.list_dir", return_value=["a.md"]) as list_dir:
            self.assertEqual(content.existing_news_names(), ["a.md"])
            content.existing_card_names()
        self.assertEqual([c.args for c in list_dir.call_args_list],
                         [("src/content/news", "main"), ("src/content/collaborazioni", "main")])


if __name__ == "__main__":
    unittest.main()
