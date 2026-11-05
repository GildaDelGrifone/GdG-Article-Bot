import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from telegram import BotCommand
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot import texts
from bot import agent
from bot.callbacks import handle_callback_query, handle_revision_reply
from bot.commands import drafts_command, help_command
from bot.conversation import build_conversation
from core import config, db

logger = logging.getLogger(__name__)


def setup_logging():
    handlers = [
        RotatingFileHandler(os.path.join(config.LOGS_DIR, "bot.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8"),
        logging.StreamHandler(),
    ]
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, config.LOG_LEVEL), handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def post_init(application):
    await application.bot.set_my_commands([BotCommand(name, description) for name, description in texts.COMMAND_DESCRIPTIONS])


async def on_error(update, context):
    logger.error("Errore non gestito", exc_info=context.error)


def build_application():
    """L'Application con tutti gli handler registrati (separata da main() per poterla verificare nei test)."""
    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .get_updates_read_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .connect_timeout(30.0)
        .post_init(post_init)
        .build()
    )
    admin = filters.Chat(chat_id=int(config.ADMIN_CHAT_ID))
    # Gruppo -1: le risposte a «Rivedi» vanno gestite prima della conversazione, che altrimenti le leggerebbe come brief
    application.add_handler(MessageHandler(admin & filters.REPLY & filters.TEXT & ~filters.COMMAND, handle_revision_reply), group=-1)
    application.add_handler(build_conversation(extra_fallbacks=[agent.conversation_fallback()]))
    application.add_handler(CallbackQueryHandler(handle_callback_query, pattern=r"^art_"))
    application.add_handler(CommandHandler(["start", "help"], help_command, filters=admin))
    application.add_handler(CommandHandler("bozze", drafts_command, filters=admin))
    # l'assistente (/scrivi) sta nello stesso gruppo, dopo la conversazione: i messaggi liberi sono suoi solo se non c'è una raccolta aperta
    for handler in agent.handlers():
        application.add_handler(handler)
    application.add_error_handler(on_error)
    return application


def main():
    setup_logging()
    missing = config.missing_settings("telegram")
    if missing:
        logger.error("Impostazioni mancanti nel file .env: %s", ", ".join(missing))
        sys.exit(1)
    db.init_db()
    logger.info("Avvio GdG-Article-Bot (AI: %s)", config.AI_PROVIDER)
    build_application().run_polling()


if __name__ == "__main__":
    main()
