import functools

from core import config
from bot import texts


def is_admin_chat(update) -> bool:
    chat = update.effective_chat
    return bool(chat and config.ADMIN_CHAT_ID and str(chat.id) == str(config.ADMIN_CHAT_ID))


def admin_only(notify=False):
    """Limita un handler a ADMIN_CHAT_ID; con notify=True chi scrive in privato riceve «Non sei autorizzato.»."""
    def decorator(handler):
        @functools.wraps(handler)
        async def wrapper(update, context):
            if not is_admin_chat(update):
                message = update.effective_message
                if notify and message and update.effective_chat and update.effective_chat.type == "private":
                    await message.reply_text(texts.UNAUTHORIZED)
                return None
            return await handler(update, context)
        return wrapper
    return decorator
