import unittest
from unittest.mock import patch

import main
from bot import texts
from tests.support.config import patched_config
from tests.support.telegram import make_context


class TestBuildApplication(unittest.TestCase):
    def build(self):
        with patched_config(TELEGRAM_BOT_TOKEN="123456:ABCdefGhIJKlmNoPQRsTUVwxyZ", ADMIN_CHAT_ID="999"):
            return main.build_application()

    def test_handlers_are_registered_in_the_right_groups(self):
        application = self.build()
        early = [type(h).__name__ for h in application.handlers[-1]]
        normal = [type(h).__name__ for h in application.handlers[0]]
        self.assertEqual(early, ["MessageHandler"])  # risposte a «Rivedi», prima della conversazione
        self.assertEqual(normal, ["ConversationHandler", "CallbackQueryHandler", "CommandHandler", "CommandHandler"])
        self.assertIn(main.on_error, application.error_handlers)

    def test_callback_handler_only_takes_article_buttons(self):
        application = self.build()
        handler = application.handlers[0][1]
        self.assertTrue(handler.pattern.match("art_publish_1"))
        self.assertIsNone(handler.pattern.match("altro_1"))


class TestPostInit(unittest.IsolatedAsyncioTestCase):
    async def test_commands_are_registered(self):
        application = make_context()
        await main.post_init(application)
        commands = application.bot.set_my_commands.call_args.args[0]
        self.assertEqual([(c.command, c.description) for c in commands], texts.COMMAND_DESCRIPTIONS)


class TestMain(unittest.TestCase):
    def test_missing_settings_stop_the_start(self):
        with patch("main.setup_logging"), patch("main.config.missing_settings", return_value=["TELEGRAM_BOT_TOKEN"]), \
                patch("main.build_application") as build, self.assertLogs("main", "ERROR") as logs, \
                self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("TELEGRAM_BOT_TOKEN", logs.output[0])
        build.assert_not_called()

    def test_start_initialises_the_db_and_polls(self):
        with patch("main.setup_logging"), patch("main.config.missing_settings", return_value=[]), \
                patch("main.db.init_db") as init_db, patch("main.build_application") as build:
            main.main()
        init_db.assert_called_once_with()
        build.return_value.run_polling.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
