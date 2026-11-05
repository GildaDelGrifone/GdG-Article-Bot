import asyncio
import logging
import re

from telegram import ForceReply
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop

from bot import texts
from bot.auth import admin_only
from bot.cards import card_text_from_draft, send_card
from bot.keyboards import DISCARD_PREFIX, PUBLISH_PREFIX, REVISE_PREFIX, draft_keyboard
from core import db, pipeline
from core.ai.base import AIQuotaError
from core.ai.provider import get_provider

logger = logging.getLogger(__name__)


def _is_not_modified(error):
    return "not modified" in str(error).lower()


async def _append_to_card(query, suffix, keep_keyboard=False):
    """Aggiunge `suffix` al messaggio della scheda; di default toglie i pulsanti."""
    message = query.message
    base = message.text_html if getattr(message, "text_html", None) else (message.text or "")
    try:
        await query.edit_message_text(
            text=base + suffix, parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=query.message.reply_markup if keep_keyboard else None,
        )
    except BadRequest as e:
        if not _is_not_modified(e):
            raise


async def _publish(query, article_id):
    try:
        url = await asyncio.to_thread(pipeline.publish, article_id)
    except pipeline.PipelineError as e:
        await _append_to_card(query, texts.format_action_error(e), keep_keyboard=True)
        return
    except Exception:
        logger.exception("Pubblicazione fallita")
        await _append_to_card(query, texts.format_action_error(texts.GENERIC_ERROR), keep_keyboard=True)
        return
    await _append_to_card(query, texts.format_published_suffix(url))


async def _discard(query, article_id):
    try:
        outcome = await asyncio.to_thread(pipeline.discard, article_id)
    except pipeline.PipelineError as e:
        await _append_to_card(query, texts.format_action_error(e), keep_keyboard=True)
        return
    except Exception:
        logger.exception("Eliminazione della bozza fallita")
        await _append_to_card(query, texts.format_action_error(texts.GENERIC_ERROR), keep_keyboard=True)
        return
    await _append_to_card(query, texts.ALREADY_PUBLISHED_SUFFIX if outcome == "published" else texts.DISCARDED_SUFFIX)


async def _ask_revision(query, article_id):
    await query.message.reply_text(texts.format_revise_prompt(article_id), reply_markup=ForceReply(selective=True))


ROUTES = (
    (PUBLISH_PREFIX, _publish),
    (REVISE_PREFIX, _ask_revision),
    (DISCARD_PREFIX, _discard),
)


@admin_only()
async def handle_callback_query(update, context):
    query = update.callback_query
    data = query.data or ""
    for prefix, handler in ROUTES:
        if data.startswith(prefix) and data[len(prefix):].isdigit():
            await query.answer(texts.POPUP_WORKING if handler is not _ask_revision else None)
            await handler(query, int(data[len(prefix):]))
            return
    await query.answer()


async def _refresh_card(context, article_id, draft, fallback_message):
    """Aggiorna la scheda originale con la bozza rivista; se non si può modificare ne invia una nuova."""
    article = db.get_article(article_id)
    text = card_text_from_draft(draft)
    if article and article["chat_id"] and article["message_id"]:
        try:
            await context.bot.edit_message_text(
                chat_id=article["chat_id"], message_id=article["message_id"], text=text, parse_mode="HTML",
                reply_markup=draft_keyboard(article_id), disable_web_page_preview=True,
            )
            return
        except BadRequest as e:
            if _is_not_modified(e):
                return
            logger.warning("Scheda non modificabile, ne invio una nuova: %s", e)
    sent = await send_card(fallback_message, text, article_id)
    db.update_article(article_id, chat_id=sent.chat_id, message_id=sent.message_id)


@admin_only()
async def handle_revision_reply(update, context):
    """Risposta al messaggio «Cosa vuoi cambiare nella bozza #N?»: rigenera il testo con le indicazioni."""
    message = update.message
    replied = message.reply_to_message if message else None
    match = re.search(re.escape(texts.REVISE_MARKER) + r"(\d+)", (replied.text or "") if replied else "")
    if not match or not message.text:
        return  # non è una risposta a una richiesta di revisione: lascia passare agli altri handler
    article_id = int(match.group(1))

    await message.reply_text(texts.REVISING)
    try:
        draft = await asyncio.to_thread(pipeline.revise_article, article_id, message.text)
    except AIQuotaError as e:
        await message.reply_text(texts.format_quota_alert(get_provider().name, e), parse_mode="HTML")
    except pipeline.PipelineError as e:
        await message.reply_text(texts.format_error(e), parse_mode="HTML")
    except Exception:
        logger.exception("Revisione fallita")
        await message.reply_text(texts.GENERIC_ERROR)
    else:
        await _refresh_card(context, article_id, draft, message)
        await message.reply_text(texts.REVISED)
    raise ApplicationHandlerStop
