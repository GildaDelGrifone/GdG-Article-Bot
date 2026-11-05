"""L'assistente in chat (/scrivi): gli stessi passaggi della skill write-article, dentro Telegram.

I messaggi dell'amministratore (testo, foto, file immagine) si raccolgono per qualche secondo, così un album di foto
più il testo diventa **un solo turno** dell'agente (`core/agent/loop.py`). La risposta torna come testo semplice; le
bozze che l'agente chiede di mostrare escono con i pulsanti [Pubblica][Rivedi][Scarta]: pubblicare resta un tocco.
"""
import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field

from telegram.constants import ChatAction
from telegram.ext import CommandHandler, ConversationHandler, MessageHandler, filters

from bot import texts
from bot.auth import admin_only
from bot.cards import card_text_from_row, send_card_to_chat
from bot.conversation import DRAFT_KEY, _clear, _extension_for_document
from core import config, db
from core.agent import loop
from core.agent.llm import get_agent_llm
from core.ai.base import AIQuotaError

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 3
TYPING_SECONDS = 4
MESSAGE_LIMIT = 4096
STATE_KEY = "agent_state"


@dataclass
class ChatState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    texts: list = field(default_factory=list)
    photos: list = field(default_factory=list)  # percorsi dei file arrivati e non ancora passati all'agente


def _state(application, chat_id):
    return application.bot_data.setdefault(STATE_KEY, {}).setdefault(chat_id, ChatState())


def _incoming_dir(chat_id):
    return os.path.join(loop.session_dir(chat_id), "incoming")


def split_text(text, limit=MESSAGE_LIMIT):
    """Spezza `text` in parti di al massimo `limit` caratteri, preferibilmente a capo."""
    parts = []
    text = text.strip()
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


# --- comandi -----------------------------------------------------------------------------------------------------

@admin_only(notify=True)
async def scrivi(update, context):
    """Apre la sessione dell'assistente (chiude una raccolta /news o /collab in corso); termina anche quella conversazione."""
    chat_id = update.effective_chat.id
    missing = config.missing_settings("agent")
    if missing:
        await update.message.reply_text(texts.format_missing_settings(missing), parse_mode="HTML")
        return ConversationHandler.END
    if context.user_data.get(DRAFT_KEY):
        _clear(update, context)
    if loop.session_status(chat_id) == "active":
        await update.message.reply_text(texts.AGENT_ALREADY_OPEN)
    else:
        await asyncio.to_thread(loop.start_session, chat_id, get_agent_llm())
        await update.message.reply_text(texts.START_AGENT, parse_mode="HTML")
    return ConversationHandler.END


@admin_only(notify=True)
async def fine(update, context):
    """Azzera la sessione (cronologia e foto): nessun token speso per il contesto di una conversazione finita."""
    chat_id = update.effective_chat.id
    _cancel_flush(context.application, chat_id)
    state = _state(context.application, chat_id)
    state.texts.clear()
    state.photos.clear()
    existed = await asyncio.to_thread(loop.reset_session, chat_id)
    await update.message.reply_text(texts.AGENT_ENDED if existed else texts.AGENT_NO_SESSION)


# --- messaggi in arrivo ------------------------------------------------------------------------------------------

def _cancel_flush(application, chat_id):
    for job in application.job_queue.get_jobs_by_name(f"agent_{chat_id}"):
        job.schedule_removal()


def _schedule_flush(application, chat_id):
    _cancel_flush(application, chat_id)
    application.job_queue.run_once(_flush, DEBOUNCE_SECONDS, chat_id=chat_id, name=f"agent_{chat_id}")


async def _ensure_session(update, context):
    """True se si può procedere: sessione attiva, oppure scaduta e riaperta (con avviso)."""
    chat_id = update.effective_chat.id
    status = loop.session_status(chat_id)
    if status == "none":
        await update.message.reply_text(texts.AGENT_NO_SESSION)
        return False
    if status == "expired":
        await asyncio.to_thread(loop.start_session, chat_id, get_agent_llm())
        await update.message.reply_text(texts.AGENT_SESSION_RESTARTED)
    return True


def _session_active(update, context):
    """Il filtro dei messaggi: l'assistente risponde solo se la sessione esiste e non c'è una raccolta /news o /collab."""
    return not context.user_data.get(DRAFT_KEY)


async def _download(update, telegram_file, extension):
    chat_id = update.effective_chat.id
    folder = _incoming_dir(chat_id)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{uuid.uuid4().hex[:8]}{extension}")
    file = await telegram_file.get_file()
    data = await file.download_as_bytearray()
    with open(path, "wb") as f:
        f.write(data)
    return path


async def _receive_image(update, context, telegram_file, extension):
    if not _session_active(update, context) or not await _ensure_session(update, context):
        return
    chat_id = update.effective_chat.id
    state = _state(context.application, chat_id)
    try:
        path = await _download(update, telegram_file, extension)
    except Exception as e:
        logger.error("Download dell'immagine fallito: %s", e)
        await update.message.reply_text(texts.IMAGE_DOWNLOAD_ERROR)
        return
    state.photos.append(path)
    caption = (update.message.caption or "").strip()
    if caption:
        state.texts.append(caption)
    session = db.get_agent_session(chat_id)
    taken = max((image["number"] for image in session["images"]), default=0) if session else 0
    await update.message.reply_text(texts.format_image_received(taken + len(state.photos)))
    _schedule_flush(context.application, chat_id)


@admin_only()
async def receive_photo(update, context):
    await _receive_image(update, context, update.message.photo[-1], ".jpg")


@admin_only()
async def receive_document(update, context):
    extension = _extension_for_document(update.message.document)
    if not extension:
        if _session_active(update, context) and loop.session_status(update.effective_chat.id) != "none":
            await update.message.reply_text(texts.NOT_AN_IMAGE)
        return
    await _receive_image(update, context, update.message.document, extension)


@admin_only()
async def receive_text(update, context):
    if not _session_active(update, context) or not await _ensure_session(update, context):
        return
    chat_id = update.effective_chat.id
    _state(context.application, chat_id).texts.append(update.message.text.strip())
    _schedule_flush(context.application, chat_id)


# --- un turno dell'agente ----------------------------------------------------------------------------------------

async def _keep_typing(bot, chat_id):
    while True:
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:  # l'indicatore è solo di cortesia
            pass
        await asyncio.sleep(TYPING_SECONDS)


async def _flush(context):
    """Passa all'agente tutto quello che è arrivato (testo e foto) e manda la risposta."""
    chat_id = context.job.chat_id
    bot = context.bot
    state = _state(context.application, chat_id)
    async with state.lock:
        message_texts, photos = state.texts[:], state.photos[:]
        state.texts.clear()
        state.photos.clear()
        if not message_texts and not photos:
            return
        prompt = "\n\n".join(message_texts) or "(solo immagini)"
        typing = asyncio.create_task(_keep_typing(bot, chat_id))
        try:
            answer, draft_ids, _ = await asyncio.to_thread(loop.run_turn, chat_id, prompt, photos)
        except AIQuotaError as e:
            await bot.send_message(chat_id, texts.format_quota_alert(config.agent_provider(), e), parse_mode="HTML")
            return
        except Exception:
            logger.exception("Turno dell'assistente fallito")
            await bot.send_message(chat_id, texts.GENERIC_ERROR)
            return
        finally:
            typing.cancel()
            for path in photos:
                try:
                    os.remove(path)
                except OSError:
                    pass
        for part in split_text(answer or "(nessuna risposta)"):
            await bot.send_message(chat_id, part)
        for article_id in draft_ids:
            article = db.get_article(article_id)
            if not article or article["status"] != "draft":
                continue
            sent = await send_card_to_chat(bot, chat_id, card_text_from_row(article), article_id)
            db.update_article(article_id, chat_id=sent.chat_id, message_id=sent.message_id)


def handlers():
    """Gli handler dell'assistente, da registrare dopo la conversazione di /news e /collab."""
    admin = filters.Chat(chat_id=int(config.ADMIN_CHAT_ID))
    return [
        CommandHandler("scrivi", scrivi, filters=admin),
        CommandHandler(["fine", "reset"], fine, filters=admin),
        MessageHandler(admin & filters.PHOTO, receive_photo),
        MessageHandler(admin & filters.Document.ALL, receive_document),
        MessageHandler(admin & filters.TEXT & ~filters.COMMAND, receive_text),
    ]


def conversation_fallback():
    """/scrivi come uscita dalla raccolta di /news e /collab."""
    admin = filters.Chat(chat_id=int(config.ADMIN_CHAT_ID))
    return CommandHandler("scrivi", scrivi, filters=admin)

