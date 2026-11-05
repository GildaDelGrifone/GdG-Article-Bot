import os
import unittest
from unittest.mock import patch

from telegram.ext import ConversationHandler

from bot import conversation as conv
from core import db
from core.ai.base import AIQuotaError
from core.pipeline import Draft, PipelineError
from tests.support.config import patched_config
from tests.support.db import TempDBMixin
from tests.support.telegram import ADMIN_CHAT_ID, make_context, make_update, telegram_file


class ConversationTestCase(TempDBMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        patcher = patched_config(ADMIN_CHAT_ID=ADMIN_CHAT_ID, GEMINI_API_KEY="g")
        patcher.__enter__()
        self.addCleanup(patcher.__exit__, None, None, None)
        self.context = make_context()

    async def start(self, handler=conv.start_news):
        update = make_update()
        state = await handler(update, self.context)
        return update, state

    @property
    def draft(self):
        return self.context.user_data[conv.DRAFT_KEY]


class TestStart(ConversationTestCase):
    async def test_news_starts_collecting(self):
        update, state = await self.start()
        self.assertEqual(state, conv.COLLECTING)
        self.assertEqual((self.draft["kind"], self.draft["images"], self.draft["texts"]), ("news", [], []))
        self.assertTrue(os.path.isdir(self.draft["folder"]))
        self.assertIn("Nuova news", update.message.reply_text.call_args.args[0])
        self.assertEqual(update.message.reply_text.call_args.kwargs["parse_mode"], "HTML")

    async def test_starting_closes_the_agent_session(self):
        from core.agent import loop
        db.save_agent_session(int(ADMIN_CHAT_ID), "gemini", "m", "SYS", [], [])
        await self.start()
        self.assertEqual(loop.session_status(int(ADMIN_CHAT_ID)), "none")

    async def test_collab_asks_for_the_logo(self):
        update, _ = await self.start(conv.start_collab)
        self.assertEqual(self.draft["kind"], "collab")
        self.assertIn("logo", update.message.reply_text.call_args.args[0])

    async def test_restarting_discards_the_previous_images(self):
        await self.start()
        old_folder = self.draft["folder"]
        await conv.receive_photo(self._photo_update(), self.context)
        await self.start(conv.start_collab)
        self.assertEqual(self.draft["images"], [])
        self.assertEqual(os.listdir(old_folder), [])

    def _photo_update(self, data=b"jpeg", caption=None):
        update = make_update(caption=caption, photo=[telegram_file(b"small"), telegram_file(data)])
        return update

    async def test_outsider_cannot_start(self):
        update = make_update(chat_id="12345", chat_type="group")
        self.assertIsNone(await conv.start_news(update, self.context))
        self.assertNotIn(conv.DRAFT_KEY, self.context.user_data)


class TestCollecting(ConversationTestCase):
    async def asyncSetUp(self):
        await self.start()

    async def test_photo_is_saved_with_the_largest_size(self):
        update = make_update(photo=[telegram_file(b"small"), telegram_file(b"big-photo")])
        state = await conv.receive_photo(update, self.context)
        self.assertEqual(state, conv.COLLECTING)
        path = self.draft["images"][0]
        self.assertTrue(path.endswith("01.jpg"))
        self.assertEqual(open(path, "rb").read(), b"big-photo")
        update.message.reply_text.assert_awaited_once_with("📷 Immagine 1 ricevuta")

    async def test_images_are_numbered_and_captions_become_text(self):
        for caption in ("Prima foto", None):
            await conv.receive_photo(make_update(caption=caption, photo=[telegram_file(b"x")]), self.context)
        self.assertEqual([os.path.basename(p) for p in self.draft["images"]], ["01.jpg", "02.jpg"])
        self.assertEqual(self.draft["texts"], ["Prima foto"])

    async def test_download_failure_is_reported_and_nothing_is_added(self):
        update = make_update(photo=[telegram_file(fail=True)])
        state = await conv.receive_photo(update, self.context)
        self.assertEqual((state, self.draft["images"]), (conv.COLLECTING, []))
        self.assertIn("scaricare", update.message.reply_text.call_args.args[0])

    async def test_image_document_keeps_its_extension(self):
        document = telegram_file(b"png-bytes")
        document.file_name, document.mime_type = "Logo Partner.PNG", "image/png"
        await conv.receive_document(make_update(document=document), self.context)
        self.assertTrue(self.draft["images"][0].endswith("01.png"))

    async def test_document_extension_falls_back_to_the_mime_type(self):
        document = telegram_file(b"x")
        document.file_name, document.mime_type = "foto", "image/webp"
        await conv.receive_document(make_update(document=document), self.context)
        self.assertTrue(self.draft["images"][0].endswith("01.webp"))

    async def test_other_documents_are_refused(self):
        document = telegram_file(b"x")
        document.file_name, document.mime_type = "scheda.pdf", "application/pdf"
        update = make_update(document=document)
        state = await conv.receive_document(update, self.context)
        self.assertEqual((state, self.draft["images"]), (conv.COLLECTING, []))
        update.message.reply_text.assert_awaited_once_with("Mandami solo immagini (JPG, PNG, WEBP) o testo.")

    async def test_text_is_collected_in_order(self):
        for text in ("  Primo  ", "Secondo"):
            await conv.receive_text(make_update(text=text), self.context)
        self.assertEqual(self.draft["texts"], ["Primo", "Secondo"])

    async def test_other_message_kinds_are_refused(self):
        update = make_update()
        self.assertEqual(await conv.reject_other(update, self.context), conv.COLLECTING)
        update.message.reply_text.assert_awaited_once()


class TestGenerate(ConversationTestCase):
    async def asyncSetUp(self):
        await self.start()
        await conv.receive_text(make_update(text="Il torneo è andato bene"), self.context)
        await conv.receive_photo(make_update(photo=[telegram_file(b"x")]), self.context)

    def article(self):
        article_id = db.insert_article("news", slug="festa", branch="news/festa", title="Festa")
        return Draft("news", "festa", "Festa <1>", "Estratto", "md", branch="news/festa",
                     draft_url="https://github.com/x", public_url="https://sito/news/festa/", id=article_id)

    async def test_without_a_started_draft(self):
        context = make_context()
        update = make_update()
        self.assertEqual(await conv.generate(update, context), ConversationHandler.END)
        update.message.reply_text.assert_awaited_once_with("Nessuna bozza in corso: usa /news o /collab.")

    async def test_needs_text(self):
        self.draft["texts"].clear()
        update = make_update()
        self.assertEqual(await conv.generate(update, self.context), conv.COLLECTING)
        self.assertIn("testo", update.message.reply_text.call_args.args[0])

    async def test_collab_needs_an_image(self):
        await self.start(conv.start_collab)
        await conv.receive_text(make_update(text="Partner"), self.context)
        update = make_update()
        self.assertEqual(await conv.generate(update, self.context), conv.COLLECTING)
        self.assertIn("logo", update.message.reply_text.call_args.args[0])

    async def test_success_sends_the_card_and_cleans_up(self):
        draft = self.article()
        folder = self.draft["folder"]
        images = list(self.draft["images"])
        update = make_update()
        with patch("bot.conversation.pipeline.create_article", return_value=draft) as create:
            state = await conv.generate(update, self.context)

        self.assertEqual(state, ConversationHandler.END)
        create.assert_called_once_with("news", "Il torneo è andato bene", images)
        progress, card = update.message.reply_text.await_args_list
        self.assertIn("Sto scrivendo", progress.args[0])
        self.assertIn("<b>Festa &lt;1&gt;</b>", card.args[0])
        self.assertIn("https://github.com/x", card.args[0])
        buttons = [b.callback_data for row in card.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(buttons, [f"art_publish_{draft.id}", f"art_revise_{draft.id}", f"art_discard_{draft.id}"])
        row = db.get_article(draft.id)
        self.assertEqual((row["chat_id"], row["message_id"]), (int(ADMIN_CHAT_ID), 1001))
        self.assertNotIn(conv.DRAFT_KEY, self.context.user_data)
        self.assertFalse(os.path.exists(folder))

    async def test_pipeline_error_keeps_the_draft_so_the_admin_can_retry(self):
        update = make_update()
        with patch("bot.conversation.pipeline.create_article", side_effect=PipelineError("Upload <fallito>")):
            state = await conv.generate(update, self.context)
        self.assertEqual(state, conv.COLLECTING)
        self.assertEqual(update.message.reply_text.call_args.args[0], "❌ Upload &lt;fallito&gt;")
        self.assertEqual(len(self.draft["images"]), 1)

    async def test_quota_error_names_the_provider(self):
        update = make_update()
        with patched_config(AI_PROVIDER="claude"), \
                patch("bot.conversation.pipeline.create_article", side_effect=AIQuotaError("429")):
            state = await conv.generate(update, self.context)
        self.assertEqual(state, conv.COLLECTING)
        text = update.message.reply_text.call_args.args[0]
        self.assertIn("claude AI", text)
        self.assertIn("429", text)

    async def test_unexpected_error_is_logged_and_reported(self):
        update = make_update()
        with patch("bot.conversation.pipeline.create_article", side_effect=RuntimeError("boom")), \
                self.assertLogs("bot.conversation", "ERROR"):
            state = await conv.generate(update, self.context)
        self.assertEqual(state, conv.COLLECTING)
        self.assertIn("Errore imprevisto", update.message.reply_text.call_args.args[0])


class TestCancelAndBuild(ConversationTestCase):
    async def test_cancel_removes_the_images(self):
        await self.start()
        await conv.receive_photo(make_update(photo=[telegram_file(b"x")]), self.context)
        folder = self.draft["folder"]
        update = make_update()
        state = await conv.cancel(update, self.context)
        self.assertEqual(state, ConversationHandler.END)
        self.assertFalse(os.path.exists(folder))
        self.assertNotIn(conv.DRAFT_KEY, self.context.user_data)

    async def test_cancel_without_a_draft_is_harmless(self):
        self.assertEqual(await conv.cancel(make_update(), self.context), ConversationHandler.END)

    def test_build_conversation(self):
        handler = conv.build_conversation()
        commands = {c for h in handler.entry_points for c in h.commands}
        self.assertEqual(commands, {"news", "collab"})
        self.assertIn(conv.COLLECTING, handler.states)
        self.assertTrue(handler.allow_reentry)


if __name__ == "__main__":
    unittest.main()
