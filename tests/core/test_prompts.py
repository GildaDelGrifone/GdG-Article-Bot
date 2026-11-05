import re
import unittest

from core import prompts
from tests.support.config import patched_config

FACTS = dict(
    ASSOCIATION_ADDRESS="Via Roma 1, Torino",
    OPENING_HOURS="lunedì dalle 21",
    TELEGRAM_GROUP_URL="https://t.me/gruppo",
    ARTICLE_CONTACTS=["@Mario", "@Luisa"],
    BOOKING_BOT_NAME="Grifone Scriba",
)


class TestSystemPrompt(unittest.TestCase):
    def test_facts_are_included(self):
        with patched_config(**FACTS):
            text = prompts.build_system_prompt()
        self.assertIn("Via Roma 1, Torino", text)
        self.assertIn("lunedì dalle 21", text)
        self.assertIn("https://t.me/gruppo", text)
        self.assertIn("[@Mario](https://t.me/Mario), [@Luisa](https://t.me/Luisa)", text)
        self.assertIn('"Grifone Scriba"', text)
        self.assertNotIn("{facts}", text)

    def test_empty_facts_are_left_out(self):
        with patched_config(**{**FACTS, "BOOKING_BOT_NAME": "", "TELEGRAM_GROUP_URL": "", "ARTICLE_CONTACTS": []}):
            text = prompts.build_system_prompt()
        self.assertNotIn("booked through", text)
        self.assertNotIn("Telegram group", text)
        self.assertNotIn("Contacts", text)

    def test_hard_rules_are_present(self):
        with patched_config(**FACTS):
            text = prompts.build_system_prompt()
        for needle in ("Italian", "![alt text](img:N)", "Never write real image URLs", "NOT start with a heading"):
            self.assertIn(needle, text)


class TestUserPrompt(unittest.TestCase):
    def test_news_prompt(self):
        text = prompts.build_prompt("news", "  Abbiamo fatto il torneo.  ", 3, article_date="05-11-2026")
        self.assertIn("NEWS article", text)
        self.assertIn("Number of images: 3.", text)
        self.assertIn("Article date: 05-11-2026.", text)
        self.assertTrue(text.endswith("BRIEF:\nAbbiamo fatto il torneo."))
        self.assertNotIn('"partner"', text)

    def test_collab_prompt_has_partner_keys_and_the_style_example(self):
        text = prompts.build_prompt("collab", "Nuovo partner X", 2)
        self.assertIn("COLLABORATION", text)
        self.assertIn('"descrizione"', text)
        self.assertIn("Siamo entusiasti di annunciare", text)
        self.assertNotIn("{style_example}", text)
        self.assertNotIn("Chi è", text)  # nessuna intestazione fissa

    def test_cover_and_logo_choices(self):
        text = prompts.build_prompt("collab", "x", 3, cover=2, logo=1)
        self.assertIn("image 2 as the cover", text)
        self.assertIn("image 1 as the partner's logo", text)

    def test_logo_choice_is_ignored_for_news(self):
        self.assertNotIn("logo", prompts.build_prompt("news", "x", 3, logo=1))

    def test_revise_addendum(self):
        text = prompts.build_prompt("news", "x", 1, revise=("VECCHIO {feedback} TESTO", "più corto"))
        self.assertIn("PREVIOUS BODY:\nVECCHIO {feedback} TESTO", text)
        self.assertIn("FEEDBACK:\npiù corto", text)

    def test_problems_are_listed(self):
        text = prompts.build_prompt("news", "x", 1, problems=["manca ---", "troppo lungo"])
        self.assertIn("- manca ---\n- troppo lungo", text)

    def test_brief_with_braces_is_left_alone(self):
        text = prompts.build_prompt("news", "usa {facts} e {n_images}", 1)
        self.assertIn("usa {facts} e {n_images}", text)

    def test_no_unfilled_placeholders(self):
        with patched_config(**FACTS):
            for kind in ("news", "collab"):
                text = prompts.build_system_prompt() + prompts.build_prompt(kind, "b", 1)
                self.assertEqual(re.findall(r"\{[a-z_]+\}", text), [])


class TestStyleExample(unittest.TestCase):
    def test_example_uses_placeholders_not_urls_for_images(self):
        images = re.findall(r"!\[[^\]]*\]\(([^)]*)\)", prompts.COLLAB_STYLE_EXAMPLE)
        self.assertTrue(images)
        self.assertTrue(all(re.fullmatch(r"img:\d+", ref) for ref in images))


if __name__ == "__main__":
    unittest.main()
