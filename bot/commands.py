from bot import texts
from bot.auth import admin_only
from bot.cards import card_text_from_row, send_card
from core import db


@admin_only(notify=True)
async def help_command(update, context):
    await update.message.reply_text(texts.HELP, parse_mode="HTML")


@admin_only(notify=True)
async def drafts_command(update, context):
    """Rimanda la scheda di ogni bozza in attesa (utile dopo un riavvio o se la scheda è lontana nella chat)."""
    drafts = db.list_drafts()
    if not drafts:
        await update.message.reply_text(texts.NO_DRAFTS)
        return
    for article in reversed(drafts):
        sent = await send_card(update.message, card_text_from_row(article), article["id"])
        db.update_article(article["id"], chat_id=sent.chat_id, message_id=sent.message_id)
