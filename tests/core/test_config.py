import importlib
import os
import unittest
from unittest.mock import patch

from core import config
from tests.support.config import patched_config


class TestEnvHelpers(unittest.TestCase):
    def test_choice_is_case_insensitive(self):
        with patch.dict(os.environ, {"X": "CLAUDE"}):
            self.assertEqual(config._env_choice("X", ("gemini", "claude"), "gemini"), "claude")

    def test_choice_falls_back_and_warns(self):
        with patch.dict(os.environ, {"X": "gpt"}), self.assertLogs("core.config", "WARNING"):
            self.assertEqual(config._env_choice("X", ("gemini", "claude"), "gemini"), "gemini")

    def test_list_is_stripped_and_skips_empty(self):
        with patch.dict(os.environ, {"X": " @a, ,@b ,"}):
            self.assertEqual(config._env_list("X", ""), ["@a", "@b"])

    def test_number_typo_falls_back(self):
        with patch.dict(os.environ, {"X": "abc"}):
            self.assertEqual(config._env_int("X", 7), 7)
            self.assertEqual(config._env_float("X", 1.5), 1.5)

    def test_log_level_typo_falls_back(self):
        with patch.dict(os.environ, {"X": "loud"}):
            self.assertEqual(config._env_log_level("X"), "INFO")
        with patch.dict(os.environ, {"X": "debug"}):
            self.assertEqual(config._env_log_level("X"), "DEBUG")


class TestModuleSettings(unittest.TestCase):
    def reload(self, env):
        # Ricarica il modulo con un ambiente controllato; load_dotenv non sovrascrive le variabili già presenti.
        keys = ("AI_PROVIDER", "ASTRO_BASE_URL", "R2_PUBLIC_URL", "ARTICLE_CONTACTS", "BOOKING_BOT_NAME")
        with patch.dict(os.environ, env), patch("dotenv.load_dotenv"):
            for key in keys:
                if key not in env:
                    os.environ.pop(key, None)
            return importlib.reload(config)

    def tearDown(self):
        importlib.reload(config)

    def test_trailing_slash_is_removed(self):
        module = self.reload({"ASTRO_BASE_URL": "https://x.it/", "R2_PUBLIC_URL": "https://m.it/"})
        self.assertEqual(module.ASTRO_BASE_URL, "https://x.it")
        self.assertEqual(module.R2_PUBLIC_URL, "https://m.it")

    def test_invalid_provider_falls_back_to_gemini(self):
        module = self.reload({"AI_PROVIDER": "nope"})
        self.assertEqual(module.AI_PROVIDER, "gemini")

    def test_contacts_and_booking_bot(self):
        module = self.reload({"ARTICLE_CONTACTS": "@a,@b", "BOOKING_BOT_NAME": " Scriba "})
        self.assertEqual(module.ARTICLE_CONTACTS, ["@a", "@b"])
        self.assertEqual(module.BOOKING_BOT_NAME, "Scriba")


class TestMissingSettings(unittest.TestCase):
    FULL = dict(
        AI_PROVIDER="gemini", GEMINI_API_KEY="g", ANTHROPIC_API_KEY=None,
        R2_ACCOUNT_ID="a", R2_ACCESS_KEY_ID="k", R2_SECRET_ACCESS_KEY="s", R2_BUCKET="b", R2_PUBLIC_URL="https://m",
        ASTRO_GITHUB_REPO="o/r", ASTRO_GITHUB_TOKEN="t", TELEGRAM_BOT_TOKEN="tok", ADMIN_CHAT_ID="1",
    )

    def test_dry_run_needs_only_the_ai_key_of_the_provider(self):
        with patched_config(**{**self.FULL, "GEMINI_API_KEY": None, "R2_PUBLIC_URL": ""}):
            self.assertEqual(config.missing_settings("dry_run"), ["GEMINI_API_KEY"])

    def test_claude_provider_checks_the_anthropic_key(self):
        with patched_config(**{**self.FULL, "AI_PROVIDER": "claude"}):
            self.assertEqual(config.missing_settings("dry_run"), ["ANTHROPIC_API_KEY"])

    def test_cli_adds_r2_and_github(self):
        with patched_config(**{**self.FULL, "R2_ACCOUNT_ID": None, "ASTRO_GITHUB_TOKEN": None, "TELEGRAM_BOT_TOKEN": None}):
            self.assertEqual(config.missing_settings("cli"), ["R2_ACCOUNT_ID", "ASTRO_GITHUB_TOKEN"])

    def test_telegram_adds_token_and_admin_chat(self):
        with patched_config(**{**self.FULL, "TELEGRAM_BOT_TOKEN": None, "ADMIN_CHAT_ID": None}):
            self.assertEqual(config.missing_settings("telegram"), ["TELEGRAM_BOT_TOKEN", "ADMIN_CHAT_ID"])

    def test_nothing_missing(self):
        with patched_config(**self.FULL):
            self.assertEqual(config.missing_settings("telegram"), [])


class TestPatchedConfig(unittest.TestCase):
    def test_unknown_setting_raises(self):
        with self.assertRaises(AttributeError):
            with patched_config(NOT_A_SETTING=1):
                pass


if __name__ == "__main__":
    unittest.main()
