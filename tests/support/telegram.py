"""Oggetti di python-telegram-bot che ricevono gli handler, già collegati ad AsyncMock."""
from unittest.mock import AsyncMock, MagicMock

ADMIN_CHAT_ID = "999"
ADMIN_USER_ID = 998877

_BOT_COROUTINES = ("send_message", "edit_message_text", "set_my_commands", "get_me")


def make_bot():
    bot = MagicMock()
    for name in _BOT_COROUTINES:
        setattr(bot, name, AsyncMock())
    return bot


def make_context():
    context = MagicMock()
    context.bot = make_bot()
    context.user_data = {}
    return context


def make_user(user_id=ADMIN_USER_ID, username="admin"):
    user = MagicMock()
    user.id = user_id
    user.username = username
    return user


def make_message(text=None, caption=None, chat_id=ADMIN_CHAT_ID, message_id=1, reply_to=None, photo=None,
                 document=None):
    """Un Message con i campi multimediali esplicitamente None (un MagicMock li inventerebbe)."""
    message = MagicMock()
    message.text = text
    message.text_html = text
    message.caption = caption
    message.chat_id = int(chat_id)
    message.message_id = message_id
    message.reply_to_message = reply_to
    message.photo = photo
    message.document = document
    sent = MagicMock()
    sent.chat_id = int(chat_id)
    sent.message_id = message_id + 1000
    message.reply_text = AsyncMock(return_value=sent)
    return message


def make_update(chat_id=ADMIN_CHAT_ID, chat_type="private", user=None, **message_kwargs):
    update = MagicMock()
    update.message = make_message(chat_id=chat_id, **message_kwargs)
    update.effective_message = update.message
    update.effective_chat.id = int(chat_id)
    update.effective_chat.type = chat_type
    update.effective_user = user if user is not None else make_user()
    return update


def callback_update(data, chat_id=ADMIN_CHAT_ID, text="Scheda bozza", message_id=123):
    """(update, query) per la pressione di un pulsante inline."""
    query = MagicMock()
    query.data = data
    query.message = make_message(text=text, chat_id=chat_id, message_id=message_id)
    query.message.reply_markup = "TASTIERA"
    for name in ("answer", "edit_message_text"):
        setattr(query, name, AsyncMock())
    update = MagicMock()
    update.callback_query = query
    update.effective_chat.id = int(chat_id)
    update.effective_chat.type = "private"
    update.effective_message = query.message
    update.effective_user = make_user()
    return update, query


def telegram_file(data=b"image-bytes", fail=False):
    """Un oggetto Photo/Document la cui get_file().download_as_bytearray() restituisce `data`."""
    holder = MagicMock()
    remote = MagicMock()
    if fail:
        holder.get_file = AsyncMock(side_effect=RuntimeError("rete"))
    else:
        remote.download_as_bytearray = AsyncMock(return_value=bytearray(data))
        holder.get_file = AsyncMock(return_value=remote)
    return holder
