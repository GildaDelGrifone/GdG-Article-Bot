import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from bot import agent, texts
from bot.conversation import DRAFT_KEY
from core import db
from core.agent import loop
from core.ai.base import AIQuotaError
from tests.support.config import patched_config
from tests.support.db import TempDBMixin
from tests.support.telegram import ADMIN_CHAT_ID, make_context, make_update, telegram_file

CHAT = int(ADMIN_CHAT_ID)
FULL = dict(ADMIN_CHAT_ID=ADMIN_CHAT_ID)


class FakeLLM:
    name = "fake"
    model = "fake-1"


class AgentTestCase(TempDBMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        patcher = patched_config(**FULL)
        patcher.__enter__()
        self.addCleanup(patcher.__exit__, None, None, None)
        self.context = make_context()
        self.context.application.bot_data = {}
        patcher = patch("bot.agent.get_agent_llm", return_value=FakeLLM())
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("bot.agent.config.missing_settings", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_session(self):
        loop.start_session(CHAT, FakeLLM())

    def state(self):
        return agent._state(self.context.application, CHAT)


class TestSplitText(unittest.TestCase):
    def test_short_text_is_one_part(self):
        self.assertEqual(agent.split_text("ciao"), ["ciao"])

    def test_long_text_splits_at_newlines_under_the_limit(self):
        text = "\n".join(["riga " * 20] * 100)
        parts = agent.split_text(text, limit=500)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertEqual("\n".join(parts).split(), text.split())

    def test_text_without_newlines_is_cut_at_the_limit(self):
        self.assertEqual([len(p) for p in agent.split_text("a" * 25, limit=10)], [10, 10, 5])


class TestScrivi(AgentTestCase):
    async def test_opens_a_session_and_ends_a_collection_in_progress(self):
        update = make_update(text="/scrivi")
        self.context.user_data[DRAFT_KEY] = {"kind": "news", "images": [], "texts": [], "folder": os.path.join(self.temp_dir, "inbox")}
        result = await agent.scrivi(update, self.context)
        self.assertEqual(result, ConversationHandler.END)
        self.assertNotIn(DRAFT_KEY, self.context.user_data)
        self.assertEqual(loop.session_status(CHAT), "active")
        self.assertEqual(update.message.reply_text.call_args.args[0], texts.START_AGENT)

    async def test_an_open_session_is_kept(self):
        self.open_session()
        db.save_agent_session(CHAT, "fake", "fake-1", "SYS", [{"role": "user"}], [])
        update = make_update(text="/scrivi")
        await agent.scrivi(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], texts.AGENT_ALREADY_OPEN)
        self.assertEqual(len(db.get_agent_session(CHAT)["messages"]), 1)

    async def test_missing_settings_are_reported(self):
        update = make_update(text="/scrivi")
        with patch("bot.agent.config.missing_settings", return_value=["ANTHROPIC_API_KEY"]):
            await agent.scrivi(update, self.context)
        self.assertIn("ANTHROPIC_API_KEY", update.message.reply_text.call_args.args[0])
        self.assertEqual(loop.session_status(CHAT), "none")

    async def test_outsiders_are_ignored(self):
        update = make_update(chat_id="5", chat_type="group", text="/scrivi")
        await agent.scrivi(update, self.context)
        update.message.reply_text.assert_not_awaited()


class TestFine(AgentTestCase):
    async def test_resets_the_session_and_drops_what_was_waiting(self):
        self.open_session()
        os.makedirs(loop.session_dir(CHAT))
        self.state().texts.append("in attesa")
        self.state().photos.append("/x.jpg")
        update = make_update(text="/fine")
        await agent.fine(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], texts.AGENT_ENDED)
        self.assertEqual(loop.session_status(CHAT), "none")
        self.assertFalse(os.path.exists(loop.session_dir(CHAT)))
        self.assertEqual((self.state().texts, self.state().photos), ([], []))

    async def test_without_a_session(self):
        update = make_update(text="/fine")
        await agent.fine(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], texts.AGENT_NO_SESSION)

    async def test_keeps_the_drafts(self):
        article_id = db.insert_article("news", slug="x", branch="news/x", title="T")
        self.open_session()
        await agent.fine(make_update(text="/fine"), self.context)
        self.assertEqual(db.get_article(article_id)["status"], "draft")

    def test_reset_is_an_alias(self):
        with patched_config(**FULL):
            commands = [set(h.commands) for h in agent.handlers() if hasattr(h, "commands")]
        self.assertIn({"fine", "reset"}, commands)


class TestIntake(AgentTestCase):
    async def test_text_without_a_session_asks_for_scrivi(self):
        update = make_update(text="ciao")
        await agent.receive_text(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], texts.AGENT_NO_SESSION)
        self.assertEqual(self.state().texts, [])

    async def test_text_is_collected_and_a_flush_scheduled(self):
        self.open_session()
        await agent.receive_text(make_update(text=" nota uno "), self.context)
        await agent.receive_text(make_update(text="nota due"), self.context)
        self.assertEqual(self.state().texts, ["nota uno", "nota due"])
        self.assertEqual(self.context.application.job_queue.run_once.call_count, 2)
        self.assertEqual(self.context.application.job_queue.run_once.call_args.args[1], agent.DEBOUNCE_SECONDS)

    async def test_ignored_while_a_news_collection_is_open(self):
        self.open_session()
        self.context.user_data[DRAFT_KEY] = {"kind": "news"}
        update = make_update(text="per /news")
        await agent.receive_text(update, self.context)
        self.assertEqual(self.state().texts, [])
        update.message.reply_text.assert_not_awaited()

    async def test_expired_session_restarts_with_a_notice_and_keeps_the_message(self):
        self.open_session()
        update = make_update(text="ancora io")
        with patched_config(AGENT_IDLE_HOURS=-1):
            await agent.receive_text(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], texts.AGENT_SESSION_RESTARTED)
        self.assertEqual(self.state().texts, ["ancora io"])

    async def test_photos_are_downloaded_numbered_and_the_caption_is_kept(self):
        self.open_session()
        first = make_update(photo=[telegram_file(b"1")], caption="didascalia")
        await agent.receive_photo(first, self.context)
        second = make_update(photo=[telegram_file(b"2")])
        await agent.receive_photo(second, self.context)
        self.assertEqual(first.message.reply_text.call_args.args[0], texts.format_image_received(1))
        self.assertEqual(second.message.reply_text.call_args.args[0], texts.format_image_received(2))
        self.assertEqual(len(self.state().photos), 2)
        self.assertTrue(all(os.path.isfile(p) for p in self.state().photos))
        self.assertEqual(self.state().texts, ["didascalia"])

    async def test_numbering_continues_after_the_images_already_in_the_session(self):
        db.save_agent_session(CHAT, "fake", "fake-1", "SYS", [], [{"number": 1, "path": "a"}, {"number": 2, "path": "b"}])
        update = make_update(photo=[telegram_file()])
        await agent.receive_photo(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], texts.format_image_received(3))

    async def test_failed_download(self):
        self.open_session()
        update = make_update(photo=[telegram_file(fail=True)])
        await agent.receive_photo(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], texts.IMAGE_DOWNLOAD_ERROR)
        self.assertEqual(self.state().photos, [])

    async def test_documents_accept_any_supported_image_and_refuse_others(self):
        self.open_session()
        good = make_update(document=SimpleNamespace(file_name="banner.JFIF", mime_type=None, **vars(telegram_file())))
        good.message.document = telegram_file()
        good.message.document.file_name, good.message.document.mime_type = "banner.jfif", "image/jpeg"
        await agent.receive_document(good, self.context)
        self.assertEqual(len(self.state().photos), 1)
        self.assertTrue(self.state().photos[0].endswith(".jfif"))
        bad = make_update()
        bad.message.document = telegram_file()
        bad.message.document.file_name, bad.message.document.mime_type = "note.pdf", "application/pdf"
        await agent.receive_document(bad, self.context)
        self.assertEqual(bad.message.reply_text.call_args.args[0], texts.NOT_AN_IMAGE)
        self.assertEqual(len(self.state().photos), 1)


class TestFlush(AgentTestCase):
    def job_context(self):
        context = make_context()
        context.application = self.context.application
        context.job = SimpleNamespace(chat_id=CHAT)
        context.bot.send_chat_action = AsyncMock()
        context.bot.send_message.return_value = SimpleNamespace(chat_id=CHAT, message_id=1001)
        return context

    async def flush(self, run_turn):
        context = self.job_context()
        with patch("bot.agent.loop.run_turn", side_effect=run_turn) as turn:
            await agent._flush(context)
        return context, turn

    async def test_one_turn_for_texts_and_photos_then_answer_and_cards(self):
        article_id = db.insert_article("news", slug="festa", branch="news/festa", title="Festa", draft_url="d", public_url="p")
        photo = os.path.join(self.temp_dir, "p.jpg")
        open(photo, "w").close()
        self.state().texts += ["uno", "due"]
        self.state().photos.append(photo)
        context, turn = await self.flush(lambda chat, text, photos: ("Ecco fatto", [article_id], [1]))
        turn.assert_called_once_with(CHAT, "uno\n\ndue", [photo])
        sent = [c.args[1] for c in context.bot.send_message.call_args_list]
        self.assertEqual(sent[0], "Ecco fatto")
        card = context.bot.send_message.call_args_list[1]
        self.assertIn("Festa", card.args[1])
        self.assertIsNotNone(card.kwargs["reply_markup"])
        self.assertEqual(db.get_article(article_id)["message_id"], 1001)
        self.assertFalse(os.path.exists(photo))
        self.assertEqual((self.state().texts, self.state().photos), ([], []))

    async def test_only_photos_still_make_a_turn(self):
        self.state().photos.append("/non/esiste.jpg")
        _, turn = await self.flush(lambda chat, text, photos: ("ok", [], []))
        self.assertEqual(turn.call_args.args[1], "(solo immagini)")

    async def test_nothing_waiting_does_nothing(self):
        _, turn = await self.flush(lambda *a: ("x", [], []))
        turn.assert_not_called()

    async def test_closed_drafts_are_not_shown_again(self):
        article_id = db.insert_article("news", slug="festa", branch="news/festa", title="Festa")
        db.update_article(article_id, status="published")
        self.state().texts.append("x")
        context, _ = await self.flush(lambda *a: ("ok", [article_id], []))
        self.assertEqual(context.bot.send_message.call_count, 1)

    async def test_long_answers_are_split(self):
        self.state().texts.append("x")
        context, _ = await self.flush(lambda *a: ("riga\n" * 2000, [], []))
        self.assertGreater(context.bot.send_message.call_count, 1)
        self.assertTrue(all(len(c.args[1]) <= agent.MESSAGE_LIMIT for c in context.bot.send_message.call_args_list))

    async def test_quota_error_alerts_and_keeps_the_session(self):
        self.open_session()
        self.state().texts.append("x")

        def quota(*a):
            raise AIQuotaError("crediti")

        with patched_config(AGENT_PROVIDER="claude"):
            context, _ = await self.flush(quota)
        self.assertIn("claude", context.bot.send_message.call_args.args[1])
        self.assertEqual(loop.session_status(CHAT), "active")

    async def test_unexpected_error_is_logged_and_reported(self):
        self.state().texts.append("x")

        def crash(*a):
            raise RuntimeError("bug")

        with self.assertLogs("bot.agent", "ERROR"):
            context, _ = await self.flush(crash)
        self.assertEqual(context.bot.send_message.call_args.args[1], texts.GENERIC_ERROR)


class TestRegistration(unittest.TestCase):
    def test_handlers_and_fallback(self):
        with patched_config(**FULL):
            handlers = agent.handlers()
            fallback = agent.conversation_fallback()
        self.assertEqual([type(h).__name__ for h in handlers], ["CommandHandler", "CommandHandler", "MessageHandler",
                                                                  "MessageHandler", "MessageHandler"])
        self.assertIn("scrivi", fallback.commands)

    def test_commands_are_in_the_menu(self):
        names = [name for name, _ in texts.COMMAND_DESCRIPTIONS]
        self.assertIn("scrivi", names)
        self.assertIn("fine", names)
        self.assertIn("/fine", texts.HELP)


if __name__ == "__main__":
    unittest.main()
