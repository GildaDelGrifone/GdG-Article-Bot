import unittest

from bot import commands
from core import db
from tests.support.config import patched_config
from tests.support.db import TempDBMixin
from tests.support.telegram import ADMIN_CHAT_ID, make_context, make_update


class TestCommands(TempDBMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        patcher = patched_config(ADMIN_CHAT_ID=ADMIN_CHAT_ID)
        patcher.__enter__()
        self.addCleanup(patcher.__exit__, None, None, None)

    async def test_help(self):
        update = make_update()
        await commands.help_command(update, make_context())
        text = update.message.reply_text.call_args.args[0]
        for needle in ("/news", "/collab", "/genera", "copertina:", "logo:"):
            self.assertIn(needle, text)
        self.assertEqual(update.message.reply_text.call_args.kwargs["parse_mode"], "HTML")

    async def test_no_drafts(self):
        update = make_update()
        await commands.drafts_command(update, make_context())
        update.message.reply_text.assert_awaited_once_with("Nessuna bozza in attesa.")

    async def test_pending_drafts_are_listed_oldest_first_and_cards_are_remembered(self):
        first = db.insert_article("news", slug="a", branch="news/a", title="Prima", draft_url="https://x/a")
        second = db.insert_article("collab", slug="b", branch="collaborazione/b", title="Seconda", card_slug="partner")
        done = db.insert_article("news", slug="c", branch="news/c", title="Pubblicata", status="published")
        update = make_update()
        await commands.drafts_command(update, make_context())

        sent = update.message.reply_text.await_args_list
        self.assertEqual(len(sent), 2)
        self.assertIn("Prima", sent[0].args[0])
        self.assertIn("Seconda", sent[1].args[0])
        self.assertIn("Scheda partner", sent[1].args[0])
        self.assertEqual(sent[0].kwargs["reply_markup"].inline_keyboard[0][0].callback_data, f"art_publish_{first}")
        self.assertEqual(db.get_article(first)["message_id"], 1001)
        self.assertIsNone(db.get_article(done)["message_id"])
        self.assertIsNotNone(db.get_article(second)["message_id"])


if __name__ == "__main__":
    unittest.main()
