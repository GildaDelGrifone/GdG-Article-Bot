import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telegram import ForceReply
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop

from bot import callbacks, texts
from core import db
from core.ai.base import AIQuotaError
from core.pipeline import Draft, PipelineError
from tests.support.config import patched_config
from tests.support.db import TempDBMixin
from tests.support.telegram import ADMIN_CHAT_ID, callback_update, make_context, make_update


class CallbackTestCase(TempDBMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        patcher = patched_config(ADMIN_CHAT_ID=ADMIN_CHAT_ID, AI_PROVIDER="gemini")
        patcher.__enter__()
        self.addCleanup(patcher.__exit__, None, None, None)
        self.context = make_context()


class TestPublishAndDiscard(CallbackTestCase):
    async def press(self, data):
        update, query = callback_update(data)
        await callbacks.handle_callback_query(update, self.context)
        return query

    async def test_publish_success_removes_the_buttons(self):
        with patch("bot.callbacks.pipeline.publish", return_value="https://sito/news/festa/") as publish:
            query = await self.press("art_publish_7")
        publish.assert_called_once_with(7)
        query.answer.assert_awaited_once()
        kwargs = query.edit_message_text.call_args.kwargs
        self.assertTrue(kwargs["text"].startswith("Scheda bozza"))
        self.assertIn("PUBBLICATO", kwargs["text"])
        self.assertIn("https://sito/news/festa/", kwargs["text"])
        self.assertIsNone(kwargs["reply_markup"])

    async def test_publish_schedules_the_live_check(self):
        with patch("bot.callbacks.pipeline.publish", return_value="https://sito/news/festa/"):
            await self.press("art_publish_7")
        run_once = self.context.job_queue.run_once
        run_once.assert_called_once()
        self.assertEqual(run_once.call_args.args[1], callbacks.LIVE_CHECK_DELAY)
        self.assertEqual(run_once.call_args.kwargs["data"]["url"], "https://sito/news/festa/")

    async def test_failed_publish_schedules_no_check(self):
        with patch("bot.callbacks.pipeline.publish", side_effect=PipelineError("no")):
            await self.press("art_publish_7")
        self.context.job_queue.run_once.assert_not_called()

    async def test_publish_error_keeps_the_buttons_for_a_retry(self):
        with patch("bot.callbacks.pipeline.publish", side_effect=PipelineError("merge <fallito>")):
            query = await self.press("art_publish_7")
        kwargs = query.edit_message_text.call_args.kwargs
        self.assertIn("❌ merge &lt;fallito&gt;", kwargs["text"])
        self.assertEqual(kwargs["reply_markup"], "TASTIERA")

    async def test_unexpected_publish_error_is_logged(self):
        with patch("bot.callbacks.pipeline.publish", side_effect=RuntimeError("boom")), \
                self.assertLogs("bot.callbacks", "ERROR"):
            query = await self.press("art_publish_7")
        self.assertIn("Errore imprevisto", query.edit_message_text.call_args.kwargs["text"])

    async def test_discard(self):
        with patch("bot.callbacks.pipeline.discard", return_value="discarded") as discard:
            query = await self.press("art_discard_7")
        discard.assert_called_once_with(7)
        self.assertIn("BOZZA SCARTATA", query.edit_message_text.call_args.kwargs["text"])
        self.assertIsNone(query.edit_message_text.call_args.kwargs["reply_markup"])

    async def test_discard_of_a_published_article(self):
        with patch("bot.callbacks.pipeline.discard", return_value="published"):
            query = await self.press("art_discard_7")
        self.assertIn("già stato pubblicato", query.edit_message_text.call_args.kwargs["text"])

    async def test_discard_error(self):
        with patch("bot.callbacks.pipeline.discard", side_effect=PipelineError("no")):
            query = await self.press("art_discard_7")
        self.assertEqual(query.edit_message_text.call_args.kwargs["reply_markup"], "TASTIERA")

    async def test_not_modified_is_ignored(self):
        update, query = callback_update("art_publish_7")
        query.edit_message_text.side_effect = BadRequest("Message is not modified")
        with patch("bot.callbacks.pipeline.publish", return_value="https://x"):
            await callbacks.handle_callback_query(update, self.context)

    async def test_other_edit_errors_propagate(self):
        update, query = callback_update("art_publish_7")
        query.edit_message_text.side_effect = BadRequest("Chat not found")
        with patch("bot.callbacks.pipeline.publish", return_value="https://x"), self.assertRaises(BadRequest):
            await callbacks.handle_callback_query(update, self.context)

    async def test_revise_button_asks_for_feedback_with_force_reply(self):
        update, query = callback_update("art_revise_7")
        await callbacks.handle_callback_query(update, self.context)
        text = query.message.reply_text.call_args.args[0]
        self.assertIn(texts.REVISE_MARKER + "7", text)
        self.assertIsInstance(query.message.reply_text.call_args.kwargs["reply_markup"], ForceReply)

    async def test_unknown_or_malformed_data_only_answers(self):
        for data in ("art_publish_abc", "altro_1", "art_publish_"):
            update, query = callback_update(data)
            with patch("bot.callbacks.pipeline.publish") as publish:
                await callbacks.handle_callback_query(update, self.context)
            publish.assert_not_called()
            query.answer.assert_awaited_once()
            query.edit_message_text.assert_not_called()

    async def test_outsiders_are_ignored(self):
        update, query = callback_update("art_publish_7", chat_id="12345")
        with patch("bot.callbacks.pipeline.publish") as publish:
            await callbacks.handle_callback_query(update, self.context)
        publish.assert_not_called()
        query.answer.assert_not_called()


class TestRevisionReply(CallbackTestCase):
    def setUp(self):
        super().setUp()
        self.article_id = db.insert_article("news", slug="festa", branch="news/festa", title="Festa",
                                            chat_id=int(ADMIN_CHAT_ID), message_id=321)
        self.draft = Draft("news", "festa", "Nuovo titolo", "Estratto", "md", branch="news/festa",
                           draft_url="https://github.com/x", public_url="https://sito/news/festa/", id=self.article_id)

    def reply_update(self, text="più breve", prompt=None):
        prompt_message = make_update(text=prompt or texts.format_revise_prompt(self.article_id)).message
        return make_update(text=text, reply_to=prompt_message)

    async def revise(self, update):
        with self.assertRaises(ApplicationHandlerStop):
            await callbacks.handle_revision_reply(update, self.context)

    async def test_feedback_regenerates_and_edits_the_original_card(self):
        update = self.reply_update("più breve")
        with patch("bot.callbacks.pipeline.revise_article", return_value=self.draft) as revise:
            await self.revise(update)
        revise.assert_called_once_with(self.article_id, "più breve")
        kwargs = self.context.bot.edit_message_text.call_args.kwargs
        self.assertEqual((kwargs["chat_id"], kwargs["message_id"]), (int(ADMIN_CHAT_ID), 321))
        self.assertIn("Nuovo titolo", kwargs["text"])
        self.assertEqual(len(kwargs["reply_markup"].inline_keyboard), 2)
        replies = [c.args[0] for c in update.message.reply_text.await_args_list]
        self.assertEqual(replies, [texts.REVISING, texts.REVISED])

    async def test_card_that_cannot_be_edited_is_sent_again(self):
        self.context.bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
        update = self.reply_update()
        with patch("bot.callbacks.pipeline.revise_article", return_value=self.draft), \
                self.assertLogs("bot.callbacks", "WARNING"):
            await self.revise(update)
        row = db.get_article(self.article_id)
        self.assertEqual(row["message_id"], 1001)
        card_call = [c for c in update.message.reply_text.await_args_list if "Nuovo titolo" in c.args[0]]
        self.assertEqual(len(card_call), 1)

    async def test_unchanged_card_is_fine(self):
        self.context.bot.edit_message_text.side_effect = BadRequest("Message is not modified")
        update = self.reply_update()
        with patch("bot.callbacks.pipeline.revise_article", return_value=self.draft):
            await self.revise(update)
        self.assertEqual(update.message.reply_text.await_args_list[-1].args[0], texts.REVISED)

    async def test_pipeline_error(self):
        update = self.reply_update()
        with patch("bot.callbacks.pipeline.revise_article", side_effect=PipelineError("già pubblicata")):
            await self.revise(update)
        self.assertEqual(update.message.reply_text.await_args_list[-1].args[0], "❌ già pubblicata")
        self.context.bot.edit_message_text.assert_not_called()

    async def test_quota_error(self):
        update = self.reply_update()
        with patch("bot.callbacks.pipeline.revise_article", side_effect=AIQuotaError("429")):
            await self.revise(update)
        self.assertIn("gemini AI", update.message.reply_text.await_args_list[-1].args[0])

    async def test_unexpected_error(self):
        update = self.reply_update()
        with patch("bot.callbacks.pipeline.revise_article", side_effect=RuntimeError("x")), \
                self.assertLogs("bot.callbacks", "ERROR"):
            await self.revise(update)
        self.assertIn("Errore imprevisto", update.message.reply_text.await_args_list[-1].args[0])

    async def test_other_replies_pass_through(self):
        for update in (self.reply_update(prompt="Un altro messaggio #3"), make_update(text="non è una risposta")):
            with patch("bot.callbacks.pipeline.revise_article") as revise:
                self.assertIsNone(await callbacks.handle_revision_reply(update, self.context))
            revise.assert_not_called()
            update.message.reply_text.assert_not_called()

    async def test_outsiders_are_ignored(self):
        update = self.reply_update()
        update.effective_chat.id = 12345
        with patch("bot.callbacks.pipeline.revise_article") as revise:
            self.assertIsNone(await callbacks.handle_revision_reply(update, self.context))
        revise.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestLiveCheck(CallbackTestCase):
    def job_context(self, attempt):
        context = make_context()
        context.job = SimpleNamespace(data={"url": "https://s/x/", "chat_id": 9, "attempt": attempt})
        return context

    async def test_reachable_page_is_reported(self):
        context = self.job_context(1)
        with patch("bot.callbacks.storage.is_reachable", return_value=True):
            await callbacks._check_live(context)
        self.assertIn("✅", context.bot.send_message.call_args.args[1])
        context.job_queue.run_once.assert_not_called()

    async def test_first_failure_retries_once_then_warns(self):
        first = self.job_context(1)
        with patch("bot.callbacks.storage.is_reachable", return_value=False):
            await callbacks._check_live(first)
        first.bot.send_message.assert_not_awaited()
        self.assertEqual(first.job_queue.run_once.call_args.kwargs["data"]["attempt"], 2)
        last = self.job_context(2)
        with patch("bot.callbacks.storage.is_reachable", return_value=False):
            await callbacks._check_live(last)
        self.assertIn("⚠️", last.bot.send_message.call_args.args[1])
        last.job_queue.run_once.assert_not_called()
