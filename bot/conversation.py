import asyncio
import logging
import os
import shutil

from telegram.ext import CommandHandler, ConversationHandler, MessageHandler, filters

from bot import texts
from bot.auth import admin_only
from bot.cards import card_text_from_draft, send_card
from core import config, db, pipeline
from core.ai.base import AIQuotaError
from core.ai.provider import get_provider

logger = logging.getLogger(__name__)

COLLECTING = 0
DRAFT_KEY = "draft"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIME_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _inbox(update):
    """Cartella temporanea delle immagini ricevute per la bozza in corso di questa chat/utente."""
    user = update.effective_user.id if update.effective_user else 0
    return os.path.join(config.TMP_DIR, "inbox", f"{update.effective_chat.id}_{user}".replace("-", "m"))


def _clear(update, context):
    draft = context.user_data.pop(DRAFT_KEY, None)
    shutil.rmtree(draft["folder"] if draft else _inbox(update), ignore_errors=True)


def _extension_for_document(document):
    extension = os.path.splitext(document.file_name or "")[1].lower()
    return extension if extension in IMAGE_EXTENSIONS else MIME_EXTENSIONS.get(document.mime_type)


async def _start(update, context, kind, intro):
    _clear(update, context)
    folder = _inbox(update)
    os.makedirs(folder, exist_ok=True)
    context.user_data[DRAFT_KEY] = {"kind": kind, "images": [], "texts": [], "folder": folder}
    await update.message.reply_text(intro, parse_mode="HTML")
    return COLLECTING


@admin_only(notify=True)
async def start_news(update, context):
    return await _start(update, context, "news", texts.START_NEWS)


@admin_only(notify=True)
async def start_collab(update, context):
    return await _start(update, context, "collab", texts.START_COLLAB)


async def _save_image(update, context, telegram_file, extension):
    draft = context.user_data[DRAFT_KEY]
    number = len(draft["images"]) + 1
    path = os.path.join(draft["folder"], f"{number:02d}{extension}")
    try:
        file = await telegram_file.get_file()
        data = await file.download_as_bytearray()
        with open(path, "wb") as f:
            f.write(data)
    except Exception as e:
        logger.error("Download dell'immagine fallito: %s", e)
        await update.message.reply_text(texts.IMAGE_DOWNLOAD_ERROR)
        return COLLECTING
    draft["images"].append(path)
    caption = (update.message.caption or "").strip()
    if caption:
        draft["texts"].append(caption)
    await update.message.reply_text(texts.format_image_received(number))
    return COLLECTING


@admin_only()
async def receive_photo(update, context):
    return await _save_image(update, context, update.message.photo[-1], ".jpg")


@admin_only()
async def receive_document(update, context):
    document = update.message.document
    extension = _extension_for_document(document)
    if not extension:
        await update.message.reply_text(texts.NOT_AN_IMAGE)
        return COLLECTING
    return await _save_image(update, context, document, extension)


@admin_only()
async def receive_text(update, context):
    context.user_data[DRAFT_KEY]["texts"].append(update.message.text.strip())
    return COLLECTING


@admin_only()
async def generate(update, context):
    draft = context.user_data.get(DRAFT_KEY)
    message = update.message
    if not draft:
        await message.reply_text(texts.NO_ACTIVE_DRAFT)
        return ConversationHandler.END
    brief = "\n".join(draft["texts"]).strip()
    if not brief:
        await message.reply_text(texts.NEED_TEXT)
        return COLLECTING
    if draft["kind"] == "collab" and not draft["images"]:
        await message.reply_text(texts.NEED_LOGO_IMAGE)
        return COLLECTING

    await message.reply_text(texts.GENERATING)
    try:
        result = await asyncio.to_thread(pipeline.create_article, draft["kind"], brief, draft["images"])
    except AIQuotaError as e:
        await message.reply_text(texts.format_quota_alert(get_provider().name, e), parse_mode="HTML")
        return COLLECTING
    except pipeline.PipelineError as e:
        await message.reply_text(texts.format_error(e), parse_mode="HTML")
        return COLLECTING
    except Exception:
        logger.exception("Generazione dell'articolo fallita")
        await message.reply_text(texts.GENERIC_ERROR)
        return COLLECTING

    sent = await send_card(message, card_text_from_draft(result), result.id)
    db.update_article(result.id, chat_id=sent.chat_id, message_id=sent.message_id)
    _clear(update, context)
    return ConversationHandler.END


@admin_only()
async def cancel(update, context):
    _clear(update, context)
    await update.message.reply_text(texts.CANCELLED)
    return ConversationHandler.END


@admin_only()
async def reject_other(update, context):
    await update.message.reply_text(texts.NOT_AN_IMAGE)
    return COLLECTING


def build_conversation():
    """Il ConversationHandler di /news e /collab (creato all'avvio, quando ADMIN_CHAT_ID è già noto)."""
    admin = filters.Chat(chat_id=int(config.ADMIN_CHAT_ID))
    return ConversationHandler(
        entry_points=[CommandHandler("news", start_news, filters=admin), CommandHandler("collab", start_collab, filters=admin)],
        states={
            COLLECTING: [
                MessageHandler(admin & filters.PHOTO, receive_photo),
                MessageHandler(admin & filters.Document.ALL, receive_document),
                MessageHandler(admin & filters.TEXT & ~filters.COMMAND, receive_text),
                CommandHandler("genera", generate, filters=admin),
                CommandHandler("annulla", cancel, filters=admin),
                MessageHandler(admin & ~filters.COMMAND, reject_other),
            ],
        },
        fallbacks=[CommandHandler("annulla", cancel, filters=admin)],
        allow_reentry=True,
    )
