import unittest
from unittest.mock import MagicMock

from core import validate
from core.ai.base import AIError, AIQuotaError
from tests.support.config import patched_config

NEWS_BODY = """\
Ciao a tutti da **Gilda del Grifone**, ecco il racconto del torneo.

## Il torneo

Una giornata bellissima.

![Giocatori al tavolo](img:2)
_Il tavolo principale_

## Cosa viene dopo

Vi aspettiamo [sul gruppo](https://t.me/gruppo).

---

Venite a trovarci in sede! 🦅"""

COLLAB_BODY = """\
Annunciamo la collaborazione con **Aces Games**!

### Il primo gioco

Bellissimo.

![Il manuale](img:2)

### Come provarli?

Visitate [Aces Games](https://acesgames.it). Grazie ad Aces Games!

---

Ci vediamo al tavolo 🦅"""


def news(**overrides):
    return {"title": "Torneo di primavera", "excerpt": "Un bel torneo", "cover_index": 1, "body": NEWS_BODY, **overrides}


def collab(**overrides):
    partner = {"nome": "Aces Games", "url": "https://acesgames.it", "descrizione": "Casa editrice italiana.", "logo_index": 3}
    result = {"title": "La Gilda incontra Aces Games", "excerpt": "Nuova collaborazione", "cover_index": 1,
              "body": COLLAB_BODY, "partner": partner}
    result.update(overrides)
    return result


class TestCheckNews(unittest.TestCase):
    def problems(self, result=None, n_images=3):
        return validate.check_article(result or news(), "news", n_images)

    def assertProblem(self, text, problems):
        self.assertTrue(any(text in problem for problem in problems), problems)

    def test_valid_article(self):
        self.assertEqual(self.problems(), [])

    def test_missing_or_long_fields(self):
        self.assertProblem("manca il titolo", self.problems(news(title="  ")))
        self.assertProblem("titolo supera", self.problems(news(title="x" * 81)))
        self.assertProblem("manca l'estratto", self.problems(news(excerpt="")))
        self.assertProblem("estratto supera", self.problems(news(excerpt="x" * 161)))
        self.assertProblem("manca il testo", self.problems(news(body="")))

    def test_cover_index_range(self):
        self.assertEqual(self.problems(news(cover_index=None)), [])
        self.assertProblem("cover_index", self.problems(news(cover_index=4)))
        self.assertProblem("cover_index", self.problems(news(cover_index=0)))
        self.assertProblem("cover_index", self.problems(news(cover_index=True)))
        self.assertProblem("cover_index", self.problems(news(cover_index=1), n_images=0))

    def test_body_must_not_start_with_a_heading(self):
        self.assertProblem("inizia con un titolo", self.problems(news(body="## Subito\n" + NEWS_BODY)))

    def test_h1_is_rejected(self):
        body = NEWS_BODY.replace("## Il torneo", "# Il torneo")
        problems = self.problems(news(body=body))
        self.assertProblem("livello 1", problems)

    def test_section_count_for_news(self):
        one = NEWS_BODY.replace("## Cosa viene dopo", "Cosa viene dopo")
        self.assertProblem("sezioni", self.problems(news(body=one)))
        five = NEWS_BODY.replace("---", "".join(f"## S{i}\ntesto\n\n" for i in range(4)) + "---")
        self.assertProblem("sezioni", self.problems(news(body=five)))

    def test_level_three_sections_count(self):
        body = NEWS_BODY.replace("## ", "### ")
        self.assertEqual(self.problems(news(body=body)), [])

    def test_horizontal_rule_rules(self):
        self.assertProblem("--- prima", self.problems(news(body=NEWS_BODY.replace("---\n\n", ""))))
        twice = NEWS_BODY.replace("## Cosa", "---\n\n## Cosa")
        self.assertProblem("--- prima", self.problems(news(body=twice)))
        no_closing = NEWS_BODY.split("---")[0] + "---\n"
        self.assertProblem("manca il paragrafo di chiusura", self.problems(news(body=no_closing)))

    def test_closing_emoji_is_required(self):
        self.assertProblem("🦅", self.problems(news(body=NEWS_BODY.replace(" 🦅", ""))))

    def test_image_references(self):
        bad_range = NEWS_BODY.replace("img:2", "img:9")
        self.assertProblem("inesistente", self.problems(news(body=bad_range)))
        real_url = NEWS_BODY.replace("img:2", "https://x.it/a.jpg")
        self.assertProblem("indirizzo reale", self.problems(news(body=real_url)))
        twice = NEWS_BODY.replace("## Cosa viene dopo", "![Ancora](img:2)\n\n## Cosa viene dopo")
        self.assertProblem("più di una volta", self.problems(news(body=twice)))

    def test_bare_urls_are_rejected_but_links_are_fine(self):
        bare = NEWS_BODY.replace("Una giornata bellissima.", "Vedi https://x.it per info.")
        self.assertProblem("senza link Markdown", self.problems(news(body=bare)))

    def test_html_is_rejected(self):
        html = NEWS_BODY.replace("Una giornata bellissima.", "Una <b>giornata</b> bellissima.")
        self.assertProblem("HTML", self.problems(news(body=html)))


class TestCheckCollab(unittest.TestCase):
    def problems(self, result=None, n_images=3):
        return validate.check_article(result or collab(), "collab", n_images)

    def assertProblem(self, text, problems):
        self.assertTrue(any(text in problem for problem in problems), problems)

    def test_valid_collaboration_with_free_headings(self):
        self.assertEqual(self.problems(), [])

    def test_needs_two_sections(self):
        body = COLLAB_BODY.replace("### Il primo gioco", "Il primo gioco")
        self.assertProblem("almeno 2 sezioni", self.problems(collab(body=body)))

    def test_more_than_four_sections_is_allowed(self):
        extra = "".join(f"### S{i}\ntesto\n\n" for i in range(5))
        self.assertEqual(self.problems(collab(body=COLLAB_BODY.replace("---", extra + "---"))), [])

    def test_partner_fields(self):
        partner = collab()["partner"]
        self.assertProblem("dati del partner", self.problems(collab(partner=None)))
        self.assertProblem("nome del partner", self.problems(collab(partner={**partner, "nome": ""})))
        self.assertProblem("descrizione del partner", self.problems(collab(partner={**partner, "descrizione": ""})))
        self.assertProblem("supera 200", self.problems(collab(partner={**partner, "descrizione": "x" * 201})))
        self.assertProblem("non è valido", self.problems(collab(partner={**partner, "url": "acesgames.it"})))
        self.assertEqual(self.problems(collab(partner={**partner, "url": None})), [])

    def test_logo_index(self):
        partner = collab()["partner"]
        self.assertEqual(self.problems(collab(partner={**partner, "logo_index": None})), [])
        self.assertProblem("logo_index", self.problems(collab(partner={**partner, "logo_index": 7})))
        self.assertProblem("stessa immagine", self.problems(collab(partner={**partner, "logo_index": 1})))


class TestNormalize(unittest.TestCase):
    def test_cover_override_wins(self):
        result = validate.normalize(news(cover_index=1), "news", cover=3)
        self.assertEqual(result["cover_index"], 3)

    def test_logo_override_wins_for_collab(self):
        result = validate.normalize(collab(), "collab", logo=2)
        self.assertEqual(result["partner"]["logo_index"], 2)

    def test_cover_is_removed_from_the_body_with_its_caption(self):
        result = validate.normalize(news(cover_index=2), "news")
        self.assertNotIn("img:2", result["body"])
        self.assertNotIn("Il tavolo principale", result["body"])
        self.assertNotIn("\n\n\n", result["body"])
        self.assertIn("Il torneo", result["body"])

    def test_logo_is_removed_from_the_body(self):
        body = COLLAB_BODY.replace("![Il manuale](img:2)", "![Il manuale](img:2)\n\n![Logo](img:3)")
        result = validate.normalize(collab(body=body), "collab")
        self.assertNotIn("img:3", result["body"])
        self.assertIn("img:2", result["body"])

    def test_inline_reference_is_removed(self):
        result = validate.normalize(news(cover_index=2, body="Testo ![x](img:2) fine"), "news")
        self.assertEqual(result["body"], "Testo  fine")

    def test_other_images_are_kept_and_input_is_not_mutated(self):
        original = news(cover_index=1)
        result = validate.normalize(original, "news")
        self.assertIn("img:2", result["body"])
        self.assertIsNot(result, original)

    def test_no_cover_keeps_everything(self):
        result = validate.normalize(news(cover_index=None), "news")
        self.assertEqual(result["body"], NEWS_BODY)


class TestLinkWarnings(unittest.TestCase):
    def test_unknown_links_are_reported(self):
        with patched_config(TELEGRAM_GROUP_URL="https://t.me/gruppo", ARTICLE_CONTACTS=["@Mario"]):
            body = "[a](https://noto.it) [b](https://inventato.it) [c](https://t.me/gruppo) [d](https://t.me/Mario) ![e](img:1)"
            self.assertEqual(validate.link_warnings(body, "vedi https://noto.it"), ["https://inventato.it"])


def provider_returning(*outcomes):
    provider = MagicMock()
    provider.generate.side_effect = list(outcomes)
    return provider


IMAGES = [(b"a", "image/jpeg")] * 3


class TestGenerateValid(unittest.TestCase):
    def test_first_valid_answer_is_returned_normalized(self):
        provider = provider_returning(news(cover_index=2))
        result = validate.generate_valid(provider, "news", "brief", IMAGES)
        self.assertNotIn("img:2", result["body"])
        self.assertEqual(provider.generate.call_count, 1)

    def test_prompt_and_schema_are_passed(self):
        provider = provider_returning(news())
        validate.generate_valid(provider, "news", "il mio brief", IMAGES, article_date="05-11-2026", cover=1)
        system, prompt, images, schema = provider.generate.call_args.args
        self.assertIn("Gilda del Grifone", system)
        self.assertIn("il mio brief", prompt)
        self.assertIn("image 1 as the cover", prompt)
        self.assertEqual(len(images), 3)
        self.assertNotIn("partner", schema["properties"])

    def test_retry_passes_the_problems_back(self):
        provider = provider_returning(news(body="Solo testo"), news())
        result = validate.generate_valid(provider, "news", "brief", IMAGES)
        self.assertEqual(result["title"], "Torneo di primavera")
        second_prompt = provider.generate.call_args_list[1].args[1]
        self.assertIn("had these problems", second_prompt)
        self.assertIn("sezioni", second_prompt)

    def test_gives_up_after_three_attempts(self):
        provider = provider_returning(*[news(body="Solo testo")] * 3)
        with self.assertRaises(validate.ArticleError) as ctx:
            validate.generate_valid(provider, "news", "brief", IMAGES)
        self.assertEqual(provider.generate.call_count, 3)
        self.assertTrue(ctx.exception.problems)

    def test_unusable_answer_is_retried(self):
        provider = provider_returning(AIError("JSON rotto"), news())
        self.assertEqual(validate.generate_valid(provider, "news", "brief", IMAGES)["title"], "Torneo di primavera")
        self.assertIn("JSON rotto", provider.generate.call_args_list[1].args[1])

    def test_quota_error_is_not_retried(self):
        provider = provider_returning(AIQuotaError("crediti"))
        with self.assertRaises(AIQuotaError):
            validate.generate_valid(provider, "news", "brief", IMAGES)
        self.assertEqual(provider.generate.call_count, 1)

    def test_collab_uses_the_collab_schema(self):
        provider = provider_returning(collab())
        result = validate.generate_valid(provider, "collab", "brief", IMAGES, logo=3)
        self.assertIn("partner", provider.generate.call_args.args[3]["properties"])
        self.assertNotIn("img:3", result["body"])


if __name__ == "__main__":
    unittest.main()
