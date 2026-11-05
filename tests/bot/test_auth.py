import unittest

from bot.auth import admin_only, is_admin_chat
from tests.support.config import patched_config
from tests.support.telegram import ADMIN_CHAT_ID, make_context, make_update


class TestAdminOnly(unittest.IsolatedAsyncioTestCase):
    async def run_handler(self, update, notify):
        calls = []

        @admin_only(notify=notify)
        async def handler(update, context):
            calls.append(1)
            return "ok"

        with patched_config(ADMIN_CHAT_ID=ADMIN_CHAT_ID):
            result = await handler(update, make_context())
        return result, calls

    async def test_admin_chat_passes(self):
        result, calls = await self.run_handler(make_update(), notify=True)
        self.assertEqual((result, calls), ("ok", [1]))

    async def test_other_chat_is_ignored_silently(self):
        update = make_update(chat_id="12345", chat_type="group")
        result, calls = await self.run_handler(update, notify=True)
        self.assertEqual((result, calls), (None, []))
        update.message.reply_text.assert_not_called()

    async def test_private_outsider_is_told_when_notify(self):
        update = make_update(chat_id="12345", chat_type="private")
        _, calls = await self.run_handler(update, notify=True)
        self.assertEqual(calls, [])
        update.message.reply_text.assert_awaited_once_with("Non sei autorizzato.")

    async def test_private_outsider_without_notify_gets_nothing(self):
        update = make_update(chat_id="12345", chat_type="private")
        await self.run_handler(update, notify=False)
        update.message.reply_text.assert_not_called()

    async def test_unset_admin_chat_blocks_everybody(self):
        with patched_config(ADMIN_CHAT_ID=None):
            self.assertFalse(is_admin_chat(make_update()))


if __name__ == "__main__":
    unittest.main()
